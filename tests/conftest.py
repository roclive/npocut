"""Shared offline rendering defaults."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def fake_tts_env(monkeypatch):
    monkeypatch.setenv('NPOCUT_FAKE_TTS', '1')


@pytest.fixture
def tmp_out(tmp_path):
    out = tmp_path / 'kb_out'
    out.mkdir()
    return out


@pytest.fixture
def tiny_size():
    return (64, 36)
