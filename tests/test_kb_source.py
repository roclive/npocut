import io
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import URLError

import pytest

# Keep imports working when pytest chooses tests/ as its import root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import kb_source as kb

FIXTURE = ROOT / "tests/fixtures/ontokb_reading.json"


def test_real_fixture_and_bare_list(tmp_path):
    library = kb.load_library(FIXTURE)
    assert library and any(item["kind"] == "article" for item in library)
    bare = tmp_path / "library.json"
    bare.write_text(json.dumps(library), encoding="utf-8")
    assert kb.load_library(bare) == library
    for item in library:
        script = kb.build_script(item)
        assert script["segments"][0]["heading"] == item["title"]
        assert script["segments"][-1]["text"] == item.get("url", "")
        json.dumps(script)


@pytest.mark.parametrize("payload", [{}, {"library": {}}, ["bad"]])
def test_invalid_library(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        kb.load_library(path)


def test_fetch_and_snapshot_preserve_payload(monkeypatch, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    calls = []

    def fake_open(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(kb, "urlopen", fake_open)
    assert kb.fetch_library("http://example.test/", timeout=7) == payload["library"]
    assert calls == [("http://example.test/api/reading", 7)]
    path = tmp_path / "snapshot.json"
    assert kb.main(["snapshot", "-o", str(path)]) == 0
    assert json.loads(path.read_text()) == payload


def test_network_failure(monkeypatch, capsys):
    def offline(*args, **kwargs):
        raise URLError("offline")
    monkeypatch.setattr(kb, "urlopen", offline)
    with pytest.raises(SystemExit) as exc:
        kb.main(["list"])
    assert exc.value.code == 1
    assert "offline" in capsys.readouterr().err


@pytest.mark.parametrize("lang,label,outro", [
    ("zh", "要点", "以上就是本期内容"),
    ("ja", "ポイント", "以上です"),
    ("en", "Point", "That's all."),
])
def test_script_caps_and_languages(lang, label, outro):
    item = dict(id=4, title="Title", summary="概要。", url="https://example.test",
                key_points=[f"**重点 {i}** [资料](https://example.test)" for i in range(9)])
    script = kb.build_script(item, max_points=2, lang=lang)
    points = [s for s in script["segments"] if s["kind"] == "point"]
    assert [s["heading"] for s in points] == [f"{label} 1/2", f"{label} 2/2"]
    assert all("http" not in s["narration"] and "*" not in s["narration"] for s in points)
    assert script["segments"][-1]["narration"] == outro
    assert len([s for s in kb.build_script(item)["segments"] if s["kind"] == "point"]) == 6
    assert not any(s["kind"] == "point" for s in kb.build_script(item, 0)["segments"])


def test_summary_boundaries_and_budget():
    first, second = "甲" * 79 + "。", "乙" * 69 + "！"
    segments = kb.build_script(dict(summary=first + second))["segments"]
    assert [s["text"] for s in segments if s["kind"] == "summary"] == [first, second]
    for summary in ("中" * 400, ("短句。" * 200), "English. Next? Last!", "", None):
        parts = [s for s in kb.build_script(dict(summary=summary))["segments"] if s["kind"] == "summary"]
        assert len(parts) <= 2
        assert all(0 < len(s["text"]) <= 120 for s in parts)
    assert kb._summary_parts("English. Next? Last!") == ["English. Next? Last!"]


def test_narration_cleanup():
    script = kb.build_script(dict(title="# **标题**", summary="> 阅读 `内容` https://example.test 。",
                                key_points=["- [链接](https://example.test) 和 <b>文字</b>"]))
    assert script["segments"][0]["narration"] == "标题"
    assert script["segments"][1]["narration"] == "阅读 内容 。"
    assert script["segments"][2]["narration"] == "链接 和 文字"


def test_slug():
    assert kb.slugify("Hello / World!") == "hello-world"
    assert kb.slugify("café") == "cafe"
    for value in ("中文标题", "../", "", "CON", "a" * 200):
        slug = kb.slugify(value)
        assert re.fullmatch(r"[a-z0-9-]+", slug) and len(slug) <= 80
        assert slug == kb.slugify(value)
    assert kb.slugify("中文") != kb.slugify("日本語")


def test_cli_list_and_script(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "kb_source.py"), "list", "--from-json", str(FIXTURE)],
                            capture_output=True, text=True, check=True)
    item = kb.load_library(FIXTURE)[0]
    assert f"{item['id']}\t{item['kind']}\t{item['title']}" in result.stdout
    output = tmp_path / "nested/script.json"
    subprocess.run([sys.executable, str(ROOT / "kb_source.py"), "script", str(item["id"]),
                    "--from-json", str(FIXTURE), "-o", str(output)], check=True, capture_output=True)
    assert json.loads(output.read_text()) == kb.build_script(item)
    result = subprocess.run([sys.executable, str(ROOT / "kb_source.py"), "script", "missing",
                             "--from-json", str(FIXTURE)], capture_output=True, text=True)
    assert result.returncode == 1 and "Item not found" in result.stderr
