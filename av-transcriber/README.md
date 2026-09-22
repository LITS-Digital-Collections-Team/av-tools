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

```bash
./bin/av-transcribe --setup          # project-local .venv with everything
pip install openai-whisper static-ffmpeg     # or into an existing env
brew install whisper-cpp                     # or the whisper.cpp backend
pip install weasyprint && brew install pango # only for --pdf
```

Symlink `bin/av-transcribe` onto your `PATH` if you like; it resolves symlinks
and finds its own interpreter.

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

No license chosen yet — add a `LICENSE` file before distributing. See
README.txt §19.
