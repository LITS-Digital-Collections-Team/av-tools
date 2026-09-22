#!/usr/bin/env bash
# Regenerate everything under examples/output/ from the sample input.
#
# Run this after changing anything that affects rendering, so the committed
# examples never drift from what the tool actually produces.
#
# Copyright (C) 2026 Patrick R. Wallace, Hamilton College.
# License GPLv3+: GNU GPL version 3 or later. There is NO WARRANTY.
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

cd "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IN="2026-03-14_Quarterly-Planning-Review.m4a"
STEM="${IN%.m4a}"
OUT="output"
CLI="../bin/av-transcribe"

[ -f "$IN" ] || { echo "missing $IN -- run ./make-sample-input.sh first" >&2; exit 1; }

mkdir -p "$OUT"

# Default run: paragraphs .txt, plus every machine-readable format.
"$CLI" "$IN" -o "$OUT" -m small -l en --srt --vtt --json --pdf --overwrite

# Two .txt variants, renamed so they sit alongside the default one.
"$CLI" "$IN" -o "$OUT" -m small -l en --timestamps --overwrite
mv "$OUT/$STEM.txt" "$OUT/$STEM.timestamps.txt"

"$CLI" "$IN" -o "$OUT" -m small -l en --txt-style lines --timestamps --overwrite
mv "$OUT/$STEM.txt" "$OUT/$STEM.lines.txt"

# Restore the default .txt, which the two runs above moved aside.
"$CLI" "$IN" -o "$OUT" -m small -l en --overwrite

# The .json records the absolute path of whatever machine produced it. Rewrite
# it to the bare filename so the committed example is not tied to one checkout.
python3 - "$OUT/$STEM.json" "$IN" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
with open(path) as fh:
    doc = json.load(fh)
doc["source"] = name
with open(path, "w") as fh:
    json.dump(doc, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY

echo
echo "regenerated:"
ls -1 "$OUT"
