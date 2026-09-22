# av-transcriber

Rip, normalize, and transcribe audio from any audio or video file. One command
in, a plaintext transcript out. Everything runs locally — no API keys, no
accounts.

```
media file ──ffprobe──▶ inspect ──ffmpeg──▶ 16 kHz mono PCM ──whisper──▶ .txt
                                    │                                    .pdf
                              EBU R128 loudnorm                          .srt
                                                                         .vtt
                                                                         .json
```

**[README.txt](README.txt) is the complete manual** — every flag, both
backends, output formats, library API, and troubleshooting. This page is the
quick start.

## Why the extra steps

- **Normalization matters.** Whisper degrades on quiet or uneven audio, which
  is exactly what lectern mics and handheld recorders produce. Two-pass EBU
  R128 `loudnorm` measures the whole program and applies one linear
  correction, so a quiet ceremony and a hot interview both land at −16 LUFS.
- **16 kHz mono up front.** Whisper resamples to this internally anyway. Doing
  it once in ffmpeg avoids a second decode and lets the samples be handed to
  Whisper as an array.
- **ffmpeg is located, not assumed.** Each candidate binary is executed before
  use, so a Homebrew ffmpeg whose codec libs were upgraded out from under it
  gets skipped in favor of a working one.

## Install

**System packages first**, then the tool.

```bash
# macOS
brew install python@3.12 ffmpeg

# Debian / Ubuntu / Mint / WSL  — python3-venv is NOT pulled in by python3
sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg

# Fedora / RHEL  (ffmpeg-free covers common containers; RPM Fusion for the full build)
sudo dnf install -y python3 python3-pip ffmpeg-free

# Arch / Manjaro
sudo pacman -S --needed python python-pip ffmpeg
```

```bash
./bin/av-transcribe --setup          # project-local .venv with everything
```

Then symlink it onto your `PATH` if you like (`~/.local/bin` on Linux); it
resolves symlinks and finds its own interpreter.

<details>
<summary><b>Linux / WSL notes that actually bite people</b></summary>

- **PEP 668.** Debian 12+, Ubuntu 24.04+, Fedora, and Arch block pip from
  writing into the system Python. Use `--setup`; don't reach for
  `--break-system-packages`.
- **CPU-only PyTorch.** `pip install openai-whisper` pulls the CUDA wheel by
  default — several GB wasted if you have no NVIDIA card. Install
  `pip install torch --index-url https://download.pytorch.org/whl/cpu` **first**.
- **WSL needs WSL2** (`wsl -l -v` must say `2`), and media should live on the
  Linux filesystem — reads under `/mnt/c` cross a translation layer and are
  drastically slower for large video.
- **WSL + CRLF.** Cloning with Git for Windows yields
  `bad interpreter: /usr/bin/env bash^M`. Set `core.autocrlf input`, or
  `dos2unix bin/av-transcribe examples/*.sh`.
- **WSL + NVIDIA.** Driver goes on the Windows side *only*; the CUDA runtime
  ships inside the PyTorch wheel. Then `--device cuda`.
- **WSL memory.** WSL2 caps near half your host RAM; `large-v3` wants ~6 GB. A
  bare `Killed` is the OOM killer — raise it in `.wslconfig`.
- **whisper.cpp** has no Linux package; build it with `cmake -B build && cmake
  --build build -j` (add `-DGGML_CUDA=1` for NVIDIA).

[README.txt §3](README.txt) has the full per-platform walkthrough.
</details>

Optional extras:

```bash
brew install whisper-cpp                     # whisper.cpp backend (macOS)
pip install weasyprint && brew install pango # --pdf; on Linux: libpango-1.0-0 libpangoft2-1.0-0
```

## Usage

```bash
av-transcribe talk.mp4                                   # → talk.txt
av-transcribe --pdf 2024-09-28_Board-Meeting.mov         # → .txt + typeset .pdf
av-transcribe -m medium --srt --json lecture.mov -o ./out
av-transcribe --start 60 --duration 120 -m tiny long.mp4 # quick quality check
av-transcribe ./recordings -r -m small -o ./transcripts  # batch a folder
```

| Flag | Default | Notes |
|---|---|---|
| `-m, --model` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3`/`turbo` |
| `--backend` | `auto` | `whisper` (Python) or `whisper-cpp` (Metal on Apple silicon) |
| `-l, --language` | auto-detect | Setting `en` is faster and avoids misdetection |
| `--normalize` | `ebu` | `ebu` · `fast` · `peak` · `none` |
| `--denoise` | off | 80 Hz highpass, 7.5 kHz lowpass, `afftdn` |
| `--prompt` | — | Bias spelling of names and jargon |
| `--start` / `--duration` | — | Process a slice before committing to a long run |
| `--pdf` / `--srt` / `--vtt` / `--json` | off | Additional outputs |
| `--txt-style` | `paragraphs` | `paragraphs` · `lines` · `raw` |
| `--timestamps` | off | Prefix `[h:mm:ss]` |

## Example

`examples/` holds a real input and its real output in every format, plus the
scripts that regenerate both:

```bash
./bin/av-transcribe examples/2026-03-14_Quarterly-Planning-Review.m4a \
  -o examples/output -m small -l en --srt --vtt --json --pdf
```

The sample measures −33 LUFS going in and −16 LUFS coming out, which is the
normalization stage doing the job it exists for. See
[README.txt §5](README.txt) for the walkthrough.

## Library use

```python
from av_transcriber import transcribe_file

written = transcribe_file(
    "interview.mov",
    outdir="transcripts",
    formats=("txt", "pdf", "srt"),
    model="medium",
    language="en",
)
print(written["txt"])
```

## License

Copyright © 2026 Patrick R. Wallace, Hamilton College.

- **Software** — GNU General Public License, version 3 or later
  (`SPDX-License-Identifier: GPL-3.0-or-later`). Full text in
  [COPYING](COPYING).
- **Documentation** — GNU Free Documentation License, version 1.3 or later,
  with no Invariant Sections, no Front-Cover Texts, and no Back-Cover Texts
  (`SPDX-License-Identifier: GFDL-1.3-or-later`). Full text in
  [COPYING.DOC](COPYING.DOC).

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See [LICENSE.txt](LICENSE.txt) for the summary,
including third-party component terms, and [README.txt §19](README.txt) for
the long form.
