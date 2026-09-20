from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_root_helper_uses_source_root():
    from fm9.paths import project_root, resource_path

    assert project_root() == ROOT
    assert resource_path("ui", "index.html") == ROOT / "ui" / "index.html"


def test_root_helper_uses_meipass(monkeypatch, tmp_path):
    import fm9.paths as paths

    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.project_root() == tmp_path
    assert paths.resource_path("config") == tmp_path / "config"


def test_spec_declares_required_data_and_native_collections():
    script = (ROOT / "packaging" / "build_macos.sh").read_text()
    assert "TONECOMMAND_ENTRYPOINT" in script
    assert '"$ENTRYPOINT"' in script
    for resource in ("ui", "config", "recipes"):
        assert f"--add-data \"$ROOT/{resource}:{resource}\"" in script
    assert "--collect-all rtmidi" in script
    assert "--collect-all sounddevice" in script
    assert "--collect-submodules mido.backends" in script


def test_smoke_script_has_api_and_ui_probes():
    script = (ROOT / "packaging" / "smoke.py").read_text()
    assert "/api/state" in script
    assert "http://127.0.0.1:{port}/" in script
    assert "TONECOMMAND_SIM" in script
    assert "SMOKE_OK" in script


def test_port_env_is_honoured(monkeypatch):
    import uvicorn
    import server

    called = {}
    monkeypatch.setattr(server.ai_settings, "apply_to_env", lambda: None)
    monkeypatch.setattr(server, "_pump_coremidi", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: called.update(host=host, port=port))
    monkeypatch.setenv("TONECOMMAND_PORT", "8917")
    server.main()
    assert called == {"host": "127.0.0.1", "port": 8917}


def test_workflow_is_tag_or_manual_only():
    workflow = (ROOT / ".github" / "workflows" / "bundle.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "tags: [\"v*\"]" in workflow or "tags:\n" in workflow
    assert "macos-14" in workflow
    assert "ToneCommand-macOS-${{ steps.version.outputs.version }}.zip" in workflow


def test_docs_and_changelog_describe_unsigned_app():
    setup = (ROOT / "docs" / "SETUP.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert "macOS app (no Python)" in setup
    assert "not signed by an identified developer" in setup
    assert "## Unreleased" in changelog
    assert "ToneCommand.app" in changelog
