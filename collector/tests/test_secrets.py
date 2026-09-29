from __future__ import annotations

from cuota import secrets


def zsh(tmp_path, text):
    p = tmp_path / ".zshrc"
    p.write_text(text)
    return p


def test_env_wins_over_zshrc(tmp_path):
    z = zsh(tmp_path, "export KEY=fromfile\n")
    assert secrets.resolve("KEY", env={"KEY": "fromenv"}, zshrc=z) == "fromenv"


def test_zshrc_plain_and_quoted(tmp_path):
    z = zsh(tmp_path, "export A=plain\nexport B='single q'\nexport C=\"double q\"  # note\nexport D=x # c\n")
    assert secrets.resolve("A", env={}, zshrc=z) == "plain"
    assert secrets.resolve("B", env={}, zshrc=z) == "single q"
    assert secrets.resolve("C", env={}, zshrc=z) == "double q"
    assert secrets.resolve("D", env={}, zshrc=z) == "x"


def test_last_definition_wins_and_prefix_names_do_not_match(tmp_path):
    z = zsh(tmp_path, "export KEY_2=other\nexport KEY=one\nexport KEY=two\n# export KEY=commented\n")
    assert secrets.resolve("KEY", env={}, zshrc=z) == "two"


def test_indirection_one_level_from_env_and_zshrc(tmp_path):
    z = zsh(tmp_path, "export Z_AI_API_KEY=$OTHER\nexport K=${INNER}\nexport INNER=deep\n")
    assert secrets.resolve("Z_AI_API_KEY", env={"OTHER": "viaenv"}, zshrc=z) == "viaenv"
    assert secrets.resolve("K", env={}, zshrc=z) == "deep"


def test_indirection_is_only_one_level(tmp_path):
    z = zsh(tmp_path, "export A=$B\nexport B=$C\nexport C=end\n")
    assert secrets.resolve("A", env={}, zshrc=z) == "$C"


def test_missing_returns_none(tmp_path):
    assert secrets.resolve("NOPE", env={}, zshrc=zsh(tmp_path, "export OTHER=1\n")) is None
    assert secrets.resolve("NOPE", env={}, zshrc=tmp_path / "absent") is None
    assert secrets.resolve("A", env={}, zshrc=zsh(tmp_path, "export A=$MISSING\n")) is None


def test_default_home_is_used_when_zshrc_not_given(tmp_path, monkeypatch):
    (tmp_path / ".zshrc").write_text("export HOMEKEY=h\n")
    monkeypatch.setattr(secrets.Path, "home", lambda: tmp_path)
    assert secrets.resolve("HOMEKEY", env={}) == "h"
