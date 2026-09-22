#!/usr/bin/env bash
# Regenerate examples/2026-03-14_Quarterly-Planning-Review.m4a from
# sample-script.txt.
#
# The sample is synthesized speech rather than a real recording so that it can
# be redistributed freely: no speaker to get consent from, no rights to clear.
# It is deliberately encoded 17 dB down from the synthesizer's output, which is
# roughly what a lectern mic or a phone at the back of a room produces -- so
# `--normalize ebu` has something real to correct (the sample measures about
# -33 LUFS on the way in).
#
# macOS only: uses `say` for text-to-speech. On Linux, substitute espeak-ng or
# piper and keep the rest of the pipeline identical.

set -euo pipefail

cd "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OUT="2026-03-14_Quarterly-Planning-Review.m4a"
FFMPEG="${FFMPEG_BIN:-$(command -v static_ffmpeg || command -v ffmpeg)}"

command -v say >/dev/null || { echo "this script needs macOS 'say'" >&2; exit 1; }
[ -n "$FFMPEG" ] || { echo "no ffmpeg found" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

say -v Samantha -f sample-script.txt -o "$tmp/raw.aiff"

# -17 dB: simulate a quiet room recording.
# mono / 22.05 kHz / 32 kbps AAC: keeps the committed file under 200 KB.
"$FFMPEG" -hide_banner -loglevel error -y -i "$tmp/raw.aiff" \
  -af "volume=-17dB" -ac 1 -ar 22050 -c:a aac -b:a 32k "$OUT"

echo "wrote $OUT"
