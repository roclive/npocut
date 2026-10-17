#!/usr/bin/env python3
"""Read OntoKB items and build summary-driven narration scripts, online or offline."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import unicodedata
from urllib.error import URLError
from urllib.request import urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _library(payload: object) -> list[dict]:
    library = payload.get("library") if isinstance(payload, dict) else payload
    if not isinstance(library, list) or not all(isinstance(item, dict) for item in library):
        raise ValueError("Expected a library list or a reading payload containing library")
    return library


def _fetch_payload(base_url: str, timeout: float) -> object:
    with urlopen(base_url.rstrip("/") + "/api/reading", timeout=timeout) as response:
        return json.load(response)


def fetch_library(base_url: str = DEFAULT_BASE_URL, timeout: float = 30) -> list[dict]:
    """Fetch /api/reading; network and JSON errors propagate to the caller."""
    return _library(_fetch_payload(base_url, timeout))


def load_library(path: str | Path) -> list[dict]:
    """Load a full reading snapshot or a bare library list."""
    with Path(path).open(encoding="utf-8-sig") as source:
        return _library(json.load(source))


def _plain(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"(?m)^\s*\[[^\]]+\]:.*$", "", text)
    text = re.sub(r"(?:https?://|www\.)[^\s<>。！？，；]+", "", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|>\s*|[-+*]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"[`*_~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _summary_parts(text: str) -> list[str]:
    """Keep up to two 120-character segments; prefer complete sentences.

    Oversize sentences are hard-split. Content beyond the two-segment budget
    is omitted, so arbitrarily long source summaries cannot inflate a video.
    """
    sentences = re.findall(r"[^。！？.!?]+[。！？.!?]*|[。！？.!?]+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.rstrip()
        if not current:
            sentence = sentence.lstrip()
        if current and len(current + sentence) > 120:
            parts.append(current)
            current = ""
            sentence = sentence.lstrip()
        if len(parts) >= 2:
            return parts[:2]
        while len(sentence) > 120:
            parts.append(sentence[:120])
            sentence = sentence[120:]
            if len(parts) >= 2:
                return parts[:2]
        current += sentence
    if current:
        parts.append(current)
    return parts[:2]


def build_script(item: dict, max_points: int = 6, lang: str = "zh") -> dict:
    """Build a JSON-serializable VideoScript; lang selects labels, not translation."""
    if lang not in ("zh", "ja", "en"):
        raise ValueError("lang must be zh, ja, or en")
    if max_points < 0:
        raise ValueError("max_points must be nonnegative")
    title = str(item.get("title") or item.get("id") or "Untitled")
    url = str(item.get("url") or "")
    segments = []

    def add(kind: str, heading: str, text: str, narration: str) -> None:
        segments.append(dict(kind=kind, heading=heading, text=text, narration=_plain(narration)))

    add("title", title, title, title)
    summary_label, point_label, outro = {
        "zh": ("摘要", "要点", "以上就是本期内容"),
        "ja": ("概要", "ポイント", "以上です"),
        "en": ("Summary", "Point", "That's all."),
    }[lang]
    for part in _summary_parts(_plain(item.get("summary"))):
        add("summary", summary_label, part, part)
    points = item.get("key_points") or []
    if not isinstance(points, list):
        raise ValueError("key_points must be a list")
    points = points[:max_points]
    for number, point in enumerate(points, 1):
        text = _plain(point)
        add("point", f"{point_label} {number}/{len(points)}", text, text)
    add("outro", outro, url, outro)
    return dict(id=item.get("id", ""), title=title, url=url, lang=lang, segments=segments)


def slugify(id_or_title: object) -> str:
    """Return a bounded ASCII filename component, with a stable hash fallback."""
    value = str(id_or_title)
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:80].rstrip("-")
    if not slug:
        slug = "item-" + hashlib.sha256(value.encode()).hexdigest()[:12]
    if slug in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}:
        slug = "item-" + slug
    return slug


def _write_json(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "script", "snapshot"):
        command = commands.add_parser(name)
        command.add_argument("--base-url", default=DEFAULT_BASE_URL)
        command.add_argument("--timeout", type=float, default=30)
        if name != "snapshot":
            command.add_argument("--from-json", type=Path)
        if name == "script":
            command.add_argument("id")
            command.add_argument("-o", "--output", type=Path)
            command.add_argument("--max-points", type=int, default=6)
            command.add_argument("--lang", choices=("zh", "ja", "en"), default="zh")
        if name == "snapshot":
            command.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            payload = _fetch_payload(args.base_url, args.timeout)
            _library(payload)
            _write_json(args.output, payload)
        else:
            library = (load_library(args.from_json) if args.from_json
                       else fetch_library(args.base_url, args.timeout))
            if args.command == "list":
                for item in library:
                    print(f"{item.get('id', '')}\t{item.get('kind', '')}\t{item.get('title', '')}")
            else:
                item = next((item for item in library if str(item.get("id")) == args.id), None)
                if item is None:
                    raise ValueError(f"Item not found: {args.id}")
                output = args.output or Path("kb_out") / f"{slugify(args.id)}.script.json"
                _write_json(output, build_script(item, args.max_points, args.lang))
                print(output)
    except (OSError, URLError, ValueError) as exc:
        parser.exit(1, f"kb_source: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
