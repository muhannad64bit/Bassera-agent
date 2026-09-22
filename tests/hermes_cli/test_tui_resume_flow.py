from argparse import Namespace
import sys
import types

import pytest


def _args(**overrides):
    base = {
        "continue_last": None,
        "resume": None,
        "tui": True,
    }
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture
def main_mod(monkeypatch):
    import wafi_cli.main as mod

    monkeypatch.setattr(mod, "_has_any_provider_configured", lambda: True)
    return mod


def test_cmd_chat_tui_continue_uses_latest_tui_session(monkeypatch, main_mod):
    calls = []
    captured = {}

    def fake_resolve_last(source="cli"):
        calls.append(source)
        return "20260408_235959_a1b2c3" if source == "tui" else None

    def fake_launch(resume_session_id=None, tui_dev=False, **kwargs):
        captured["resume"] = resume_session_id
        raise SystemExit(0)

    monkeypatch.setattr(main_mod, "_resolve_last_session", fake_resolve_last)
    monkeypatch.setattr(main_mod, "_resolve_session_by_name_or_id", lambda val: val)
    monkeypatch.setattr(main_mod, "_launch_tui", fake_launch)

    with pytest.raises(SystemExit):
        main_mod.cmd_chat(_args(continue_last=True))

    assert calls == ["tui"]
    assert captured["resume"] == "20260408_235959_a1b2c3"


def test_cmd_chat_tui_continue_falls_back_to_latest_cli_session(monkeypatch, main_mod):
    calls = []
    captured = {}

    def fake_resolve_last(source="cli"):
        calls.append(source)
        if source == "tui":
            return None
        if source == "cli":
            return "20260408_235959_d4e5f6"
        return None

    def fake_launch(resume_session_id=None, tui_dev=False, **kwargs):
        captured["resume"] = resume_session_id
        raise SystemExit(0)

    monkeypatch.setattr(main_mod, "_resolve_last_session", fake_resolve_last)
    monkeypatch.setattr(main_mod, "_resolve_session_by_name_or_id", lambda val: val)
    monkeypatch.setattr(main_mod, "_launch_tui", fake_launch)

    with pytest.raises(SystemExit):
        main_mod.cmd_chat(_args(continue_last=True))

    assert calls == ["tui", "cli"]
    assert captured["resume"] == "20260408_235959_d4e5f6"


def test_cmd_chat_tui_resume_resolves_title_before_launch(monkeypatch, main_mod):
    captured = {}

    def fake_launch(resume_session_id=None, tui_dev=False, **kwargs):
        captured["resume"] = resume_session_id
        raise SystemExit(0)

    monkeypatch.setattr(main_mod, "_resolve_session_by_name_or_id", lambda val: "20260409_000000_aa11bb")
    monkeypatch.setattr(main_mod, "_launch_tui", fake_launch)

    with pytest.raises(SystemExit):
        main_mod.cmd_chat(_args(resume="my t0p session"))

    assert captured["resume"] == "20260409_000000_aa11bb"


def test_print_tui_exit_summary_includes_resume_and_token_totals(monkeypatch, capsys):
    import wafi_cli.main as main_mod

    class _FakeDB:
        def get_session(self, session_id):
            assert session_id == "20260409_000001_abc123"
            return {
                "message_count": 2,
                "input_tokens": 10,
                "output_tokens": 6,
                "cache_read_tokens": 2,
                "cache_write_tokens": 2,
                "reasoning_tokens": 1,
            }

        def get_session_title(self, _session_id):
            return "demo title"

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "wafi_state", types.SimpleNamespace(SessionDB=lambda: _FakeDB()))

    main_mod._print_tui_exit_summary("20260409_000001_abc123")
    out = capsys.readouterr().out

    assert "Resume this session with:" in out
    assert "wafi --tui --resume 20260409_000001_abc123" in out
    assert 'wafi --tui -c "demo title"' in out
    assert "Tokens:         21 (in 10, out 6, cache 4, reasoning 1)" in out


def test_print_tui_exit_summary_prefers_active_session_file(monkeypatch, tmp_path, capsys):
    import wafi_cli.main as main_mod

    class _FakeDB:
        def get_session(self, session_id):
            assert session_id == "active-session"
            return {"message_count": 1}

        def get_session_title(self, _session_id):
            return ""

        def close(self):
            return None

    active = tmp_path / "active.json"
    active.write_text('{"session_id": "active-session"}', encoding="utf-8")
    monkeypatch.setitem(sys.modules, "wafi_state", types.SimpleNamespace(SessionDB=lambda: _FakeDB()))

    main_mod._print_tui_exit_summary("stale-session", str(active))
    out = capsys.readouterr().out

    assert "wafi --tui --resume active-session" in out
    assert "stale-session" not in out


def test_resolve_use_tui_honors_config_and_cli_override(monkeypatch, main_mod):
    monkeypatch.setitem(
        sys.modules,
        "wafi_cli.config",
        types.SimpleNamespace(load_config=lambda: {"display": {"interface": "tui"}}),
    )

    assert main_mod._resolve_use_tui(_args(tui=False, cli=False)) is True
    assert main_mod._resolve_use_tui(_args(tui=True, cli=True)) is False


def test_tui_need_npm_install_compares_lock_content(tmp_path, main_mod):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "entry.js").write_text("// built", encoding="utf-8")
    node_modules = tmp_path / "node_modules"
    (node_modules / "@hermes" / "ink").mkdir(parents=True)
    (node_modules / "@hermes" / "ink" / "package.json").write_text("{}", encoding="utf-8")

    wanted = {
        "packages": {
            "node_modules/@hermes/ink": {"version": "1.0.0", "peer": True},
            "node_modules/react": {"version": "19.0.0"},
        }
    }
    installed = {
        "packages": {
            "node_modules/@hermes/ink": {"version": "1.0.0"},
            "node_modules/react": {"version": "19.0.0"},
        }
    }
    (tmp_path / "package-lock.json").write_text(__import__("json").dumps(wanted), encoding="utf-8")
    (node_modules / ".package-lock.json").write_text(__import__("json").dumps(installed), encoding="utf-8")

    assert main_mod._tui_need_npm_install(tmp_path) is False
