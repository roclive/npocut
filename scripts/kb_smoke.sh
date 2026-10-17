#!/bin/sh
# Offline smoke render; optional first argument selects the output directory.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ -n "${PYTHON:-}" ]; then
    python_bin=$PYTHON
elif [ -x /opt/homebrew/Caskroom/miniconda/base/bin/python3 ]; then
    python_bin=/opt/homebrew/Caskroom/miniconda/base/bin/python3
else
    python_bin=python3
fi
export NPOCUT_FAKE_TTS=1
item_id=$("$python_bin" -c 'from kb_source import load_library; print(load_library("tests/fixtures/ontokb_reading.json")[0]["id"])')
"$python_bin" kb_video.py "$item_id" --from-json tests/fixtures/ontokb_reading.json \
    --size 640x360 --out "${1:-kb_out}"
