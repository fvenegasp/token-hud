from __future__ import annotations

import io
import urllib.error

import pytest

from cuota.readers import base, kimi
from tests.conftest import by_kind, utc

NOW = utc(2026, 9, 29, 17, 40)
SECRET = "test-SECRET-VALUE"  # noqa: S105


def raw_of(fixture):
    return {"status": fixture["_meta"]["http_status"], "body": fixture["body"]}


def test_real_fixture_5h_from_limits_detail_monthly_from_usages(fx):
    r = kimi.parse(raw_of(fx("kimi", "usages_ok")), now=NOW)
    assert (r.status, r.plan, r.source, r.error, r.fetched_at) == ("ok", None, "api", None, NOW)
    w = by_kind(r)
    assert set(w) == {"5h", "monthly"}  # limit_month_code is not exposed
    # limits[].detail (100/100 used) is the real 5 h window; usages.limit_5h (0) is not.
    assert (w["5h"].used_pct, w["5h"].state) == (100.0, "exhausted")
    assert w["5h"].resets_at.replace(microsecond=0) == utc(2026, 9, 29, 20, 3, 10)
    assert r.to_dict()["windows"][0]["resets_at"] == "2026-09-29T20:03:10Z"
    assert r.note is None
    assert w["monthly"].used_pct == pytest.approx(21.56)
    assert (w["monthly"].resets_at, w["monthly"].state) == (utc(2026, 10, 29), "active")


def test_exhausted(fx):
    f = fx("kimi", "usages_ok")
    del f["body"]["limits"]
    f["body"]["usages"]["limit_5h"]["used_ratio"] = 1
    assert by_kind(kimi.parse(raw_of(f), now=NOW))["5h"].state == "exhausted"


def test_invalid_key_401_is_auth_error(fx):
    r = kimi.parse(raw_of(fx("kimi", "usages_http401_invalid_key")), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "auth", ())


def test_http_500_is_http_error(fx):
    r = kimi.parse({"status": 500, "body": None}, now=NOW)
    assert (r.status, r.error.code) == ("error", "http")


def test_unknown_shape_is_shape_error_never_zero(fx):
    for body in ({"hello": "world"}, {"usages": {}}, {"usages": {"limit_5h": {"used_ratio": "x"}}}, None):
        r = kimi.parse({"status": 200, "body": body}, now=NOW)
        assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_ratio_out_of_range_is_shape_error(fx):
    f = fx("kimi", "usages_ok")
    del f["body"]["limits"]
    f["body"]["usages"]["limit_5h"]["used_ratio"] = 1.5
    assert kimi.parse(raw_of(f), now=NOW).error.code == "shape"


def test_missing_credential(tmp_path):
    r = kimi.read({"env": {}, "zshrc": tmp_path / "none"}, now=NOW)
    assert (r.status, r.error.code) == ("error", "missing_credential")


class FakeResp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_read_sends_bearer_and_parses(fx, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen.update(auth=req.get_header("Authorization"), url=req.full_url, timeout=timeout)
        import json

        return FakeResp(200, json.dumps(fx("kimi", "usages_ok")["body"]).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = kimi.read({"env": {"KIMI_API_KEY": SECRET}}, now=NOW)
    assert r.status == "ok"
    assert seen == {"auth": f"Bearer {SECRET}", "url": kimi.URL, "timeout": 10.0}


def test_read_http_401_becomes_auth_without_leaking_key(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error": {}}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = kimi.read({"env": {"KIMI_API_KEY": SECRET}}, now=NOW)
    assert (r.status, r.error.code) == ("error", "auth")
    assert SECRET not in repr(r)


def test_read_http_timeout_is_timeout_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    r = kimi.read({"env": {"KIMI_API_KEY": SECRET}}, now=NOW)
    assert (r.status, r.error.code) == ("error", "timeout")


def test_read_socket_timeout_and_network_error(monkeypatch):
    def slow(req, timeout):
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", slow)
    assert kimi.read({"env": {"KIMI_API_KEY": SECRET}}, now=NOW).error.code == "timeout"

    def down(req, timeout):
        raise urllib.error.URLError(OSError("no route"))

    monkeypatch.setattr("urllib.request.urlopen", down)
    assert kimi.read({"env": {"KIMI_API_KEY": SECRET}}, now=NOW).error.code == "unavailable"


def test_read_never_raises_on_unexpected_exception(monkeypatch):
    def boom(req, timeout):
        raise RuntimeError(SECRET)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = kimi.read({"env": {"KIMI_API_KEY": SECRET}}, now=NOW)
    assert r.status == "error" and SECRET not in repr(r)


def test_base_error_mapping_is_typed():
    assert base.to_error(base.ReaderError("auth", "x")).code == "auth"


def test_5h_falls_back_to_usages_with_note_when_no_limits_entry(fx):
    f = fx("kimi", "usages_ok")
    del f["body"]["limits"]
    r = kimi.parse(raw_of(f), now=NOW)
    w = by_kind(r)
    assert (w["5h"].used_pct, w["5h"].state, w["5h"].resets_at) == (
        0.0,
        "active",
        utc(2026, 9, 29, 20, 3, 10),
    )
    assert r.note == "5 h desde usages.limit_5h (sin limits[])"


def test_limits_entry_of_other_duration_does_not_count_as_5h(fx):
    f = fx("kimi", "usages_ok")
    f["body"]["limits"][0]["window"]["duration"] = 60
    r = kimi.parse(raw_of(f), now=NOW)
    assert by_kind(r)["5h"].used_pct == 0.0 and r.note


def test_limits_hour_unit_and_string_numbers(fx):
    f = fx("kimi", "usages_ok")
    f["body"]["limits"] = [
        {
            "window": {"duration": 5, "timeUnit": "TIME_UNIT_HOUR"},
            "detail": {"limit": "200", "used": "50", "resetTime": "2026-09-29T20:00:00Z"},
        }
    ]
    w = by_kind(kimi.parse(raw_of(f), now=NOW))["5h"]
    assert (w.used_pct, w.state, w.resets_at) == (25.0, "active", utc(2026, 9, 29, 20))


@pytest.mark.parametrize("limit,used", [("0", "0"), ("-5", "1"), ("abc", "1"), ("100", "x"), ("100", "150")])
def test_bad_limits_detail_is_shape_error(fx, limit, used):
    f = fx("kimi", "usages_ok")
    f["body"]["limits"][0]["detail"].update(limit=limit, used=used)
    r = kimi.parse(raw_of(f), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


def test_detail_with_remaining_only_real_shape(fx):
    f = fx("kimi", "usages_ok_remaining")
    r = kimi.parse(raw_of(f), now=utc(2026, 9, 30, 3, 45))
    w = by_kind(r)
    assert (r.status, r.note) == ("ok", None)
    assert (w["5h"].used_pct, w["5h"].state) == (0.0, "active")
    assert w["5h"].resets_at.replace(microsecond=0) == utc(2026, 9, 30, 6, 3, 10)
    assert w["monthly"].used_pct == pytest.approx(27.85)


def test_remaining_partial_use_and_used_wins_over_remaining(fx):
    f = fx("kimi", "usages_ok_remaining")
    f["body"]["limits"][0]["detail"]["remaining"] = "40"
    assert by_kind(kimi.parse(raw_of(f), now=NOW))["5h"].used_pct == 60.0
    f["body"]["limits"][0]["detail"]["used"] = "10"
    assert by_kind(kimi.parse(raw_of(f), now=NOW))["5h"].used_pct == 10.0


@pytest.mark.parametrize("remaining", ["x", "150", "-1", None])
def test_neither_used_nor_valid_remaining_is_shape_error(fx, remaining):
    f = fx("kimi", "usages_ok_remaining")
    d = f["body"]["limits"][0]["detail"]
    if remaining is None:
        del d["remaining"]
    else:
        d["remaining"] = remaining
    r = kimi.parse(raw_of(f), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())
