from __future__ import annotations

import json

import pytest

from cuota.readers import zai
from tests.conftest import by_kind, utc

NOW = utc(2026, 9, 29, 17, 40)
SECRET = "zai-SECRET-VALUE"  # noqa: S105


def raw_of(fixture):
    return {"status": fixture["_meta"]["http_status"], "body": fixture["body"]}


def test_real_fixture_weekly_exhausted_5h_idle(fx):
    r = zai.parse(raw_of(fx("zai", "quota_weekly_exhausted_5h_idle")), now=NOW)
    assert (r.status, r.plan, r.source, r.error) == ("ok", "lite", "api", None)
    w = by_kind(r)
    assert set(w) == {"5h", "weekly", "mcp"}
    assert (w["5h"].used_pct, w["5h"].resets_at, w["5h"].state) == (0.0, None, "idle")
    assert (w["weekly"].used_pct, w["weekly"].state) == (100.0, "exhausted")
    assert w["weekly"].resets_at == utc(2026, 9, 30, 2, 7, 25, 971000)
    assert (w["mcp"].used_pct, w["mcp"].state) == (0.0, "active")
    assert w["mcp"].resets_at == utc(2026, 10, 22, 2, 7, 25, 999000)


def test_5h_with_reset_time_is_active(fx):
    f = fx("zai", "quota_weekly_exhausted_5h_idle")
    for item in f["body"]["data"]["limits"]:
        if item["type"] == "TOKENS_LIMIT" and item["unit"] == 3:
            item.update(nextResetTime=1790721251000, percentage=30)
    w = by_kind(zai.parse(raw_of(f), now=NOW))["5h"]
    assert (w.used_pct, w.state, w.resets_at) == (30.0, "active", utc(2026, 9, 29, 22, 34, 11))


def test_http200_invalid_key_is_auth_error_not_ok(fx):
    f = fx("zai", "quota_http200_invalid_key")
    assert f["_meta"]["http_status"] == 200
    r = zai.parse(raw_of(f), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "auth", ())


def test_http200_success_false_other_code_is_http_error(fx):
    r = zai.parse({"status": 200, "body": {"code": 500, "success": False}}, now=NOW)
    assert (r.status, r.error.code) == ("error", "http")


def test_success_true_but_code_not_200_is_error(fx):
    f = fx("zai", "quota_weekly_exhausted_5h_idle")
    f["body"]["code"] = 500
    assert zai.parse(raw_of(f), now=NOW).status == "error"


def test_code_200_without_success_flag_is_error(fx):
    f = fx("zai", "quota_weekly_exhausted_5h_idle")
    del f["body"]["success"]
    assert zai.parse(raw_of(f), now=NOW).status == "error"


def test_real_http_401_is_auth():
    assert zai.parse({"status": 401, "body": None}, now=NOW).error.code == "auth"


def test_unknown_unit_combo_dropped_never_guessed(fx):
    f = fx("zai", "quota_weekly_exhausted_5h_idle")
    f["body"]["data"]["limits"].append({"type": "TOKENS_LIMIT", "unit": 4, "number": 2, "percentage": 77})
    r = zai.parse(raw_of(f), now=NOW)
    assert r.status == "ok" and len(r.windows) == 3
    assert 77.0 not in [w.used_pct for w in r.windows]


def test_only_unknown_limits_is_shape_error(fx):
    f = fx("zai", "quota_weekly_exhausted_5h_idle")
    f["body"]["data"]["limits"] = [{"type": "TOKENS_LIMIT", "unit": 9, "number": 9, "percentage": 5}]
    r = zai.parse(raw_of(f), now=NOW)
    assert (r.status, r.error.code, r.windows) == ("error", "shape", ())


@pytest.mark.parametrize(
    "body",
    [None, [], {"code": 200, "success": True}, {"code": 200, "success": True, "data": {"limits": "x"}}],
)
def test_unknown_shape_is_shape_error(body):
    r = zai.parse({"status": 200, "body": body}, now=NOW)
    assert r.status == "error" and r.windows == ()


def test_percentage_out_of_range_is_shape_error(fx):
    f = fx("zai", "quota_weekly_exhausted_5h_idle")
    f["body"]["data"]["limits"][2]["percentage"] = 250
    assert zai.parse(raw_of(f), now=NOW).error.code == "shape"


def test_missing_credential(tmp_path):
    r = zai.read({"env": {}, "zshrc": tmp_path / "none"}, now=NOW)
    assert (r.status, r.error.code) == ("error", "missing_credential")


def test_read_sends_raw_key_without_bearer(fx, monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def read(self):
            return json.dumps(fx("zai", "quota_weekly_exhausted_5h_idle")["body"]).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen.update(auth=req.get_header("Authorization"), timeout=timeout)
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    r = zai.read({"env": {"Z_AI_API_KEY": SECRET}, "timeout": 3}, now=NOW)
    assert r.status == "ok"
    assert seen == {"auth": SECRET, "timeout": 3.0}


def test_read_timeout(monkeypatch):
    def slow(req, timeout):
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", slow)
    r = zai.read({"env": {"Z_AI_API_KEY": SECRET}}, now=NOW)
    assert (r.status, r.error.code) == ("error", "timeout")
    assert SECRET not in repr(r)
