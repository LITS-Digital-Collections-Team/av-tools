#!/usr/bin/env python3
"""av-transcriber -- rip, normalize, and transcribe audio from any A/V file.

Pipeline
--------
1. probe    ffprobe reports duration and whether an audio stream exists.
2. extract  ffmpeg pulls the audio stream out of the container (or reads the
            audio file directly) and downmixes to 16 kHz mono PCM, which is
            exactly what Whisper resamples to internally anyway.
3. normalize  EBU R128 loudness normalization (two-pass `loudnorm` by default)
            so quiet lecterns and hot handhelds land at the same level.
4. transcribe  Whisper, via the `openai-whisper` Python package or the
            whisper.cpp `whisper-cli` binary.
5. write    plaintext .txt, plus optional .srt/.vtt/.json.

Usable as a CLI or as a library (`from av_transcriber import transcribe_file`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__version__ = "1.0.0"

# Whisper works from 16 kHz mono; producing that up front avoids a second
# resample and keeps the intermediate WAV small (~115 MB/hour).
SAMPLE_RATE = 16000
CHANNELS = 1

# EBU R128 targets. -16 LUFS is the spoken-word/podcast convention; broadcast
# delivery uses -23. TP -1.5 dBTP leaves headroom for lossy re-encoding.
DEFAULT_LUFS = -16.0
DEFAULT_TRUE_PEAK = -1.5
DEFAULT_LRA = 11.0

AUDIO_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wma", ".aif", ".aiff", ".alac", ".caf", ".amr", ".mka",
}
VIDEO_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".flv", ".wmv",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".ogv",
}
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES


class TranscriberError(RuntimeError):
    """Anything that should stop the run with a readable message."""


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

_QUIET = False


def log(msg: str) -> None:
    if not _QUIET:
        print(f"[av-transcriber] {msg}", file=sys.stderr, flush=True)


def human_time(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


# --------------------------------------------------------------------------
# binary discovery
# --------------------------------------------------------------------------

def _version_text(binary: str) -> str | None:
    """Return `binary -version` output, or None if the binary cannot run.

    Being on PATH is not enough: a Homebrew ffmpeg whose linked codec libs have
    been upgraded out from under it will dyld-abort on every invocation.
    """
    try:
        proc = subprocess.run(
            [binary, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _candidates(name: str) -> Iterable[str]:
    """Yield plausible paths for ffmpeg/ffprobe, best first."""
    env = os.environ.get(f"{name.upper()}_BIN")
    if env:
        yield env

    found = shutil.which(name)
    if found:
        yield found

    # static_ffmpeg (pip) ships self-contained builds; the wrapper scripts are
    # named static_ffmpeg / static_ffprobe and sit next to the interpreter.
    yield from (
        str(Path(sys.executable).parent / f"static_{name}"),
        shutil.which(f"static_{name}") or "",
    )

    # imageio-ffmpeg bundles a binary but exposes no ffprobe.
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore

            yield imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # pragma: no cover - optional dependency
            pass


def resolve_binary(name: str) -> tuple[str, str]:
    """Return (path, version banner) for the first candidate that runs."""
    tried: list[str] = []
    for cand in _candidates(name):
        if not cand or cand in tried:
            continue
        tried.append(cand)
        banner = _version_text(cand)
        if banner is not None:
            return cand, banner
    raise TranscriberError(
        f"No working {name} found (tried: {', '.join(tried) or 'nothing'}).\n"
        f"Install one with `brew install ffmpeg` or `pip install static-ffmpeg`, "
        f"or point {name.upper()}_BIN at a working binary."
    )


@dataclass
class FFTools:
    ffmpeg: str
    ffprobe: str
    # Not every build ships libsoxr (static-ffmpeg does not); asking for a
    # resampler that was compiled out is a hard filter-graph error, so probe it.
    soxr: bool = False

    @classmethod
    def discover(cls) -> "FFTools":
        ffmpeg, banner = resolve_binary("ffmpeg")
        ffprobe, _ = resolve_binary("ffprobe")
        tools = cls(ffmpeg=ffmpeg, ffprobe=ffprobe, soxr="--enable-libsoxr" in banner)
        log(f"ffmpeg:  {tools.ffmpeg}{'' if tools.soxr else ' (no libsoxr, using swr)'}")
        log(f"ffprobe: {tools.ffprobe}")
        return tools


def run(cmd: Sequence[str], *, capture: bool = True, desc: str = "") -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-15:])
        raise TranscriberError(f"{desc or cmd[0]} failed (exit {proc.returncode}):\n{tail}")
    return proc


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------

@dataclass
class MediaInfo:
    path: Path
    duration: float
    has_audio: bool
    has_video: bool
    audio_codec: str | None
    sample_rate: int | None
    channels: int | None

    @property
    def kind(self) -> str:
        return "video" if self.has_video else "audio"


def probe(tools: FFTools, path: Path) -> MediaInfo:
    proc = run(
        [
            tools.ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=index,codec_type,codec_name,channels,sample_rate",
            "-of", "json", str(path),
        ],
        desc="ffprobe",
    )
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)

    try:
        duration = float(data.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    return MediaInfo(
        path=path,
        duration=duration,
        has_audio=audio is not None,
        # Cover art in an MP3 shows up as a video stream; require real dimensions.
        has_video=bool(video and video.get("codec_name") not in {"mjpeg", "png", "bmp", "gif"}),
        audio_codec=(audio or {}).get("codec_name"),
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        channels=audio.get("channels") if audio else None,
    )


# --------------------------------------------------------------------------
# audio extraction + normalization
# --------------------------------------------------------------------------

def _clean_filters(denoise: bool) -> list[str]:
    """Speech-band cleanup applied before loudness measurement."""
    if not denoise:
        return []
    # 80 Hz highpass kills HVAC rumble and handling noise; 7.5 kHz lowpass sits
    # just under Nyquist for 16 kHz; afftdn is a gentle spectral denoiser.
    return ["highpass=f=80", "lowpass=f=7500", "afftdn=nf=-25"]


def measure_loudness(
    tools: FFTools,
    src: Path,
    *,
    pre_filters: Sequence[str],
    lufs: float,
    true_peak: float,
    lra: float,
    trim: Sequence[str],
) -> dict[str, str]:
    """Pass 1 of EBU R128: measure the program so pass 2 can correct linearly."""
    chain = list(pre_filters) + [
        f"loudnorm=I={lufs}:TP={true_peak}:LRA={lra}:print_format=json"
    ]
    proc = subprocess.run(
        [
            tools.ffmpeg, "-hide_banner", "-nostdin", "-y",
            *trim, "-i", str(src),
            "-map", "0:a:0", "-af", ",".join(chain),
            "-f", "null", "-",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-15:])
        raise TranscriberError(f"loudness measurement failed:\n{tail}")

    # loudnorm prints its JSON blob at the very end of stderr.
    match = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not match:
        raise TranscriberError("could not parse loudnorm measurement output")
    stats = json.loads(match[-1])
    log(
        "measured: I={input_i} LUFS  TP={input_tp} dBTP  LRA={input_lra} "
        "thresh={input_thresh}".format(**stats)
    )
    return stats


def build_audio_filter(
    mode: str,
    *,
    pre_filters: Sequence[str],
    stats: dict[str, str] | None,
    lufs: float,
    true_peak: float,
    lra: float,
    soxr: bool = False,
) -> str:
    chain = list(pre_filters)
    if mode == "ebu":
        assert stats is not None
        chain.append(
            "loudnorm="
            f"I={lufs}:TP={true_peak}:LRA={lra}"
            f":measured_I={stats['input_i']}"
            f":measured_TP={stats['input_tp']}"
            f":measured_LRA={stats['input_lra']}"
            f":measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}"
            ":linear=true:print_format=summary"
        )
    elif mode == "fast":
        # Single-pass loudnorm: dynamic, so it can pump on sparse material, but
        # it needs only one decode of the source.
        chain.append(f"loudnorm=I={lufs}:TP={true_peak}:LRA={lra}")
    elif mode == "peak":
        # Peak-normalize to the true-peak ceiling; preserves dynamics entirely.
        chain.append("dynaudnorm=f=250:g=15:p=0.9:m=10:s=5")
    elif mode == "none":
        pass
    else:  # pragma: no cover - argparse constrains this
        raise TranscriberError(f"unknown normalization mode: {mode}")

    chain.append(f"aresample={SAMPLE_RATE}:resampler=soxr" if soxr else f"aresample={SAMPLE_RATE}")
    return ",".join(chain)


def extract_audio(
    tools: FFTools,
    info: MediaInfo,
    dest: Path,
    *,
    normalize: str = "ebu",
    denoise: bool = False,
    lufs: float = DEFAULT_LUFS,
    true_peak: float = DEFAULT_TRUE_PEAK,
    lra: float = DEFAULT_LRA,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    if not info.has_audio:
        raise TranscriberError(f"{info.path.name} has no audio stream to transcribe")

    # -ss before -i seeks by keyframe (fast); -t after limits output length.
    trim: list[str] = []
    if start:
        trim += ["-ss", str(start)]
    tail: list[str] = ["-t", str(duration)] if duration else []

    pre = _clean_filters(denoise)
    stats = None
    if normalize == "ebu":
        log("normalizing: EBU R128 two-pass (pass 1/2, measuring)")
        stats = measure_loudness(
            tools, info.path, pre_filters=pre, lufs=lufs,
            true_peak=true_peak, lra=lra, trim=trim + tail,
        )
        log("normalizing: pass 2/2, applying")
    elif normalize == "none":
        log("normalizing: disabled")
    else:
        log(f"normalizing: {normalize}")

    afilter = build_audio_filter(
        normalize, pre_filters=pre, stats=stats,
        lufs=lufs, true_peak=true_peak, lra=lra, soxr=tools.soxr,
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            tools.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            *trim, "-i", str(info.path), *tail,
            "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-af", afilter,
            "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            str(dest),
        ],
        desc="ffmpeg audio extraction",
    )
    size_mb = dest.stat().st_size / 1e6
    log(f"audio ready: {dest.name} ({size_mb:.1f} MB, {SAMPLE_RATE} Hz mono)")
    return dest


# --------------------------------------------------------------------------
# transcription backends
# --------------------------------------------------------------------------

def load_wav_f32(path: Path):
    """Read our 16 kHz mono PCM WAV into the float32 array Whisper expects.

    Whisper's own loader shells out to whatever `ffmpeg` is on PATH and
    re-decodes the file. We already produced exactly the format it wants, so
    reading it here skips a decode and sidesteps a broken system ffmpeg.
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != CHANNELS or wf.getframerate() != SAMPLE_RATE:
            raise TranscriberError(
                f"expected {SAMPLE_RATE} Hz mono, got "
                f"{wf.getframerate()} Hz x{wf.getnchannels()}"
            )
        if wf.getsampwidth() != 2:
            raise TranscriberError("expected 16-bit PCM")
        raw = wf.readframes(wf.getnframes())

    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    language: str | None = None
    backend: str = ""
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()


def _pick_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import whisper  # noqa: F401

        return "whisper"
    except ImportError:
        pass
    if shutil.which("whisper-cli") or shutil.which("whisper-cpp"):
        return "whisper-cpp"
    raise TranscriberError(
        "No Whisper backend available. Install one:\n"
        "  pip install openai-whisper      # Python backend\n"
        "  brew install whisper-cpp        # whisper.cpp backend (Metal-accelerated)"
    )


def transcribe_whisper_py(
    wav: Path,
    *,
    model_name: str,
    language: str | None,
    device: str | None,
    task: str,
    initial_prompt: str | None,
) -> Transcript:
    try:
        import whisper
    except ImportError as exc:  # pragma: no cover
        raise TranscriberError("openai-whisper is not installed (`pip install openai-whisper`)") from exc

    if device in (None, "auto"):
        # Whisper's decoder uses ops that are missing or wrong on MPS today, so
        # CPU is the only reliable choice on Apple silicon. CUDA if present.
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    log(f"loading whisper model '{model_name}' on {device}")
    model = whisper.load_model(model_name, device=device)

    result = model.transcribe(
        load_wav_f32(wav),
        language=language,
        task=task,
        initial_prompt=initial_prompt,
        fp16=(device == "cuda"),
        verbose=False,
        condition_on_previous_text=True,
    )

    segments = [
        Segment(float(s["start"]), float(s["end"]), str(s["text"]).strip())
        for s in result.get("segments", [])
        if str(s.get("text", "")).strip()
    ]
    return Transcript(
        segments=segments,
        language=result.get("language"),
        backend="openai-whisper",
        model=model_name,
        meta={"device": device},
    )


def _whisper_cpp_model(model_name: str) -> Path:
    explicit = os.environ.get("WHISPER_CPP_MODEL")
    if model_name.endswith(".bin"):
        candidate = Path(model_name).expanduser()
        if candidate.is_file():
            return candidate
        raise TranscriberError(f"whisper.cpp model not found: {candidate}")

    if explicit:
        chosen = Path(explicit).expanduser()
        if not chosen.is_file():
            raise TranscriberError(f"WHISPER_CPP_MODEL is not a file: {chosen}")
        return chosen

    search = [
        Path.home() / ".cache" / "whisper-cpp" / f"ggml-{model_name}.bin",
        Path.home() / ".cache" / "whisper.cpp" / f"ggml-{model_name}.bin",
        Path.home() / "Library/Application Support/whisper-cpp" / f"ggml-{model_name}.bin",
        Path("/opt/homebrew/share/whisper-cpp/models") / f"ggml-{model_name}.bin",
        Path("/usr/local/share/whisper-cpp/models") / f"ggml-{model_name}.bin",
    ]
    for cand in search:
        if cand and cand.is_file():
            return cand
    raise TranscriberError(
        f"No whisper.cpp model 'ggml-{model_name}.bin' found. Download one:\n"
        f"  mkdir -p ~/.cache/whisper-cpp && curl -L -o ~/.cache/whisper-cpp/ggml-{model_name}.bin \\\n"
        f"    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin\n"
        f"or set WHISPER_CPP_MODEL to a .bin path."
    )


def transcribe_whisper_cpp(
    wav: Path,
    *,
    model_name: str,
    language: str | None,
    task: str,
    threads: int,
) -> Transcript:
    binary = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    if not binary:
        raise TranscriberError("whisper-cli not found (`brew install whisper-cpp`)")
    model = _whisper_cpp_model(model_name)

    with tempfile.TemporaryDirectory(prefix="whispercpp-") as tmp:
        out_base = Path(tmp) / "out"
        cmd = [
            binary, "-m", str(model), "-f", str(wav),
            "-oj", "-of", str(out_base),
            "-t", str(threads), "-pp",
        ]
        if language:
            cmd += ["-l", language]
        if task == "translate":
            cmd += ["-tr"]
        log(f"whisper.cpp: {model.name} ({threads} threads)")
        run(cmd, capture=_QUIET, desc="whisper-cli")

        out_json = out_base.with_suffix(".json")
        if not out_json.is_file():
            raise TranscriberError(
                "whisper-cli exited cleanly but wrote no JSON; the build may "
                "predate the -oj flag."
            )
        try:
            payload = json.loads(out_json.read_text())
        except json.JSONDecodeError as exc:
            raise TranscriberError(f"could not parse whisper-cli JSON output: {exc}") from exc

    segments: list[Segment] = []
    for item in payload.get("transcription", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        offsets = item.get("offsets", {})
        segments.append(
            Segment(
                start=float(offsets.get("from", 0)) / 1000.0,
                end=float(offsets.get("to", 0)) / 1000.0,
                text=text,
            )
        )
    return Transcript(
        segments=segments,
        language=(payload.get("result") or {}).get("language") or language,
        backend="whisper.cpp",
        model=model.name,
    )


# --------------------------------------------------------------------------
# output formatting
# --------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")


def to_paragraphs(
    segments: Sequence[Segment],
    *,
    gap: float = 2.0,
    max_seconds: float = 45.0,
) -> list[tuple[float, str]]:
    """Group segments into readable paragraphs.

    Whisper emits ~5-30 s segments with no notion of a paragraph. Break on a
    silence longer than `gap` that also lands on a sentence boundary, or once a
    paragraph has run past `max_seconds`.
    """
    paragraphs: list[tuple[float, str]] = []
    buf: list[str] = []
    start = 0.0
    prev_end: float | None = None

    for seg in segments:
        if not buf:
            start = seg.start
        elif (
            (prev_end is not None and seg.start - prev_end >= gap and _SENTENCE_END.search(buf[-1]))
            or (seg.end - start >= max_seconds and _SENTENCE_END.search(buf[-1]))
        ):
            paragraphs.append((start, " ".join(buf)))
            buf, start = [], seg.start
        buf.append(seg.text)
        prev_end = seg.end

    if buf:
        paragraphs.append((start, " ".join(buf)))
    return paragraphs


def render_txt(
    transcript: Transcript,
    *,
    style: str = "paragraphs",
    wrap: int = 0,
    timestamps: bool = False,
) -> str:
    if style == "raw":
        body = transcript.text
        return (textwrap.fill(body, wrap) if wrap else body) + "\n"

    if style == "lines":
        lines = []
        for seg in transcript.segments:
            prefix = f"[{human_time(seg.start)}] " if timestamps else ""
            lines.append(prefix + seg.text)
        return "\n".join(lines) + "\n"

    blocks = []
    for start, text in to_paragraphs(transcript.segments):
        if timestamps:
            text = f"[{human_time(start)}] {text}"
        blocks.append(textwrap.fill(text, wrap) if wrap else text)
    return "\n\n".join(blocks) + "\n"


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def render_srt(transcript: Transcript) -> str:
    out = []
    for i, seg in enumerate(transcript.segments, 1):
        out.append(f"{i}\n{_ts(seg.start, ',')} --> {_ts(seg.end, ',')}\n{seg.text}\n")
    return "\n".join(out)


def render_vtt(transcript: Transcript) -> str:
    out = ["WEBVTT\n"]
    for seg in transcript.segments:
        out.append(f"{_ts(seg.start, '.')} --> {_ts(seg.end, '.')}\n{seg.text}\n")
    return "\n".join(out)


_DATED_STEM = re.compile(r"^(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_\s]+(.*)$")


def derive_title(stem: str) -> tuple[str, str]:
    """Turn a filename stem into a (title, subtitle) pair.

    `2024-09-28_Innovation-Center-Groundbreaking` becomes
    ("Innovation Center Groundbreaking", "September 28, 2024").
    """
    rest, subtitle = stem, ""
    match = _DATED_STEM.match(stem)
    if match:
        year, month, day, rest = match.groups()
        try:
            date = dt.date(int(year), int(month), int(day))
            subtitle = f"{date.strftime('%B')} {date.day}, {date.year}"
        except ValueError:
            rest = stem  # not a real date; keep the stem intact

    title = re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", rest)).strip()
    return (title or stem), subtitle


# Print stylesheet for --pdf. Serif at a comfortable measure, ragged right
# with hyphenation (justification without a good hyphenator opens rivers).
PDF_CSS = """
  @page {
    size: %(page_size)s;
    margin: 0.95in 1in 0.9in;
    @bottom-center { content: counter(page); font: 9pt Georgia, serif; color: #767676; }
  }
  body {
    font: %(font_size)spt/1.6 Charter, Georgia, "Iowan Old Style", "Times New Roman", serif;
    color: #1a1a1a;
    hyphens: auto;
  }
  header { border-bottom: 1.5pt solid #1a1a1a; padding-bottom: 0.55em; margin-bottom: 1.9em; }
  h1 { font-size: 20pt; line-height: 1.2; margin: 0 0 0.28em; font-weight: 600; letter-spacing: -0.01em; }
  .subtitle { font-size: 11.5pt; color: #3d3d3d; margin: 0 0 0.35em; }
  .meta {
    font-size: 8.5pt; color: #767676; letter-spacing: 0.06em;
    text-transform: uppercase; margin: 0;
  }
  p { margin: 0 0 0.95em; text-align: left; orphans: 2; widows: 2; }
  p:first-of-type { font-size: %(lead_size)spt; }
  .ts {
    font-size: 8.5pt; color: #8a8a8a; letter-spacing: 0.04em;
    margin-right: 0.4em; white-space: nowrap;
  }
"""


def render_pdf(
    transcript: Transcript,
    info: MediaInfo,
    dest: Path,
    *,
    title: str,
    subtitle: str = "",
    timestamps: bool = False,
    page_size: str = "letter",
    font_size: float = 11.5,
) -> Path:
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise TranscriberError(
            "PDF output needs WeasyPrint: pip install weasyprint"
        ) from exc
    except OSError as exc:
        # WeasyPrint dlopens Pango/GLib at import time; a missing native library
        # surfaces here rather than as an ImportError.
        raise TranscriberError(
            f"WeasyPrint could not load its native libraries ({exc}).\n"
            "On macOS: brew install pango, then make sure "
            "DYLD_FALLBACK_LIBRARY_PATH includes /opt/homebrew/lib "
            "(the bin/av-transcribe launcher does this for you).\n"
            "On Debian/Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0"
        ) from exc

    paragraphs = to_paragraphs(transcript.segments)
    words = sum(len(text.split()) for _, text in paragraphs)

    blocks = []
    for start, text in paragraphs:
        prefix = f'<span class="ts">{human_time(start)}</span>' if timestamps else ""
        blocks.append(f"<p>{prefix}{html.escape(text)}</p>")

    meta = f"Transcript &middot; {len(paragraphs)} paragraphs &middot; {words:,} words"
    css = PDF_CSS % {
        "page_size": page_size,
        "font_size": font_size,
        "lead_size": round(font_size + 0.5, 2),
    }

    doc = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{css}</style></head><body>"
        f"<header><h1>{html.escape(title)}</h1>"
        + (f'<p class="subtitle">{html.escape(subtitle)}</p>' if subtitle else "")
        + f'<p class="meta">{meta}</p></header>'
        + "".join(blocks)
        + "</body></html>"
    )

    HTML(string=doc).write_pdf(dest)
    return dest


def render_json(transcript: Transcript, info: MediaInfo) -> str:
    return json.dumps(
        {
            "source": str(info.path),
            "duration": info.duration,
            "language": transcript.language,
            "backend": transcript.backend,
            "model": transcript.model,
            "meta": transcript.meta,
            "text": transcript.text,
            "segments": [
                {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text}
                for s in transcript.segments
            ],
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n"


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def transcribe_file(
    source: str | Path,
    *,
    outdir: str | Path | None = None,
    formats: Sequence[str] = ("txt",),
    backend: str = "auto",
    model: str = "small",
    language: str | None = None,
    task: str = "transcribe",
    device: str | None = None,
    normalize: str = "ebu",
    denoise: bool = False,
    lufs: float = DEFAULT_LUFS,
    true_peak: float = DEFAULT_TRUE_PEAK,
    lra: float = DEFAULT_LRA,
    start: float | None = None,
    duration: float | None = None,
    keep_audio: bool = False,
    txt_style: str = "paragraphs",
    wrap: int = 0,
    timestamps: bool = False,
    title: str | None = None,
    subtitle: str | None = None,
    page_size: str = "letter",
    font_size: float = 11.5,
    initial_prompt: str | None = None,
    threads: int = 0,
    overwrite: bool = False,
    tools: FFTools | None = None,
) -> dict[str, Path]:
    """Run the whole pipeline for one file. Returns {format: written path}."""
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise TranscriberError(f"input not found: {src}")

    tools = tools or FFTools.discover()
    info = probe(tools, src)
    log(
        f"input: {src.name} ({info.kind}, {human_time(info.duration)}, "
        f"audio={info.audio_codec} {info.sample_rate}Hz x{info.channels})"
    )

    out_root = Path(outdir).expanduser().resolve() if outdir else src.parent
    out_root.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    # Check every requested output before spending minutes on transcription.
    if not overwrite:
        planned = [out_root / f"{stem}.{f}" for f in formats]
        if keep_audio:
            planned.append(out_root / f"{stem}.16k.wav")
        clashes = [q for q in planned if q.exists()]
        if clashes:
            names = ", ".join(c.name for c in clashes)
            raise TranscriberError(f"already exists: {names} (use --overwrite to replace)")

    audio_dest = out_root / f"{stem}.16k.wav" if keep_audio else None
    tmpdir = None
    if audio_dest is None:
        tmpdir = tempfile.TemporaryDirectory(prefix="av-transcriber-")
        audio_dest = Path(tmpdir.name) / f"{stem}.16k.wav"

    written: dict[str, Path] = {}
    try:
        t0 = time.time()
        extract_audio(
            tools, info, audio_dest,
            normalize=normalize, denoise=denoise,
            lufs=lufs, true_peak=true_peak, lra=lra,
            start=start, duration=duration,
        )
        log(f"audio stage done in {human_time(time.time() - t0)}")

        chosen = _pick_backend(backend)
        t1 = time.time()
        if chosen in {"whisper", "openai-whisper", "py"}:
            transcript = transcribe_whisper_py(
                audio_dest, model_name=model, language=language,
                device=device, task=task, initial_prompt=initial_prompt,
            )
        elif chosen in {"whisper-cpp", "cpp"}:
            transcript = transcribe_whisper_cpp(
                audio_dest, model_name=model, language=language, task=task,
                threads=threads or max(1, (os.cpu_count() or 4) - 2),
            )
        else:
            raise TranscriberError(f"unknown backend: {chosen}")

        elapsed = time.time() - t1
        # Rate against what was actually processed, not the full source, so
        # --start/--duration slices report an honest number.
        processed = info.duration - (start or 0.0)
        if duration:
            processed = min(processed, duration)
        speed = (processed / elapsed) if elapsed > 0 and processed > 0 else 0
        log(
            f"transcribed {len(transcript.segments)} segments in "
            f"{human_time(elapsed)} ({speed:.1f}x realtime), language={transcript.language}"
        )

        renderers = {
            "txt": lambda: render_txt(transcript, style=txt_style, wrap=wrap, timestamps=timestamps),
            "srt": lambda: render_srt(transcript),
            "vtt": lambda: render_vtt(transcript),
            "json": lambda: render_json(transcript, info),
        }
        auto_title, auto_subtitle = derive_title(stem)
        for fmt in formats:
            path = out_root / f"{stem}.{fmt}"
            if fmt == "pdf":
                render_pdf(
                    transcript, info, path,
                    title=title or auto_title,
                    subtitle=subtitle if subtitle is not None else auto_subtitle,
                    timestamps=timestamps,
                    page_size=page_size,
                    font_size=font_size,
                )
            elif fmt in renderers:
                path.write_text(renderers[fmt](), encoding="utf-8")
            else:
                raise TranscriberError(f"unknown output format: {fmt}")
            written[fmt] = path
            log(f"wrote {path}")

        if keep_audio:
            written["wav"] = audio_dest
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()

    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def expand_inputs(paths: Sequence[str], recursive: bool) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            walker = p.rglob("*") if recursive else p.glob("*")
            out += sorted(
                f for f in walker
                if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES
            )
        else:
            out.append(p)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="av-transcribe",
        description="Rip, normalize, and transcribe audio from any audio/video file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              av-transcribe talk.mp4
              av-transcribe -m medium --pdf --srt lecture.mov -o ./transcripts
              av-transcribe --backend whisper-cpp -m large-v3-turbo interview.wav
              av-transcribe --start 60 --duration 120 -m tiny sample.mp4   # quick check
              av-transcribe ./recordings -r -m small -o ./transcripts       # batch
            """
        ),
    )
    p.add_argument("inputs", nargs="+", help="media file(s) or director(ies)")
    p.add_argument("-o", "--outdir", help="output directory (default: alongside input)")
    p.add_argument("-r", "--recursive", action="store_true", help="recurse into input directories")

    g = p.add_argument_group("transcription")
    g.add_argument("-m", "--model", default="small",
                   help="whisper model: tiny/base/small/medium/large-v3/turbo (default: small)")
    g.add_argument("--backend", default="auto", choices=["auto", "whisper", "whisper-cpp"],
                   help="whisper implementation (default: auto)")
    g.add_argument("-l", "--language", help="ISO code, e.g. en. Omit to auto-detect")
    g.add_argument("--task", default="transcribe", choices=["transcribe", "translate"])
    g.add_argument("--device", help="cpu | cuda | auto (Python backend)")
    g.add_argument("--threads", type=int, default=0, help="whisper.cpp thread count")
    g.add_argument("--prompt", dest="initial_prompt",
                   help="initial prompt to bias spelling of names/jargon")

    a = p.add_argument_group("audio")
    a.add_argument("--normalize", default="ebu", choices=["ebu", "fast", "peak", "none"],
                   help="loudness normalization (default: ebu, two-pass R128)")
    a.add_argument("--denoise", action="store_true",
                   help="apply highpass/lowpass/afftdn speech cleanup")
    a.add_argument("--lufs", type=float, default=DEFAULT_LUFS, help="target integrated loudness")
    a.add_argument("--true-peak", type=float, default=DEFAULT_TRUE_PEAK, help="target true peak dBTP")
    a.add_argument("--lra", type=float, default=DEFAULT_LRA, help="target loudness range")
    a.add_argument("--start", type=float, help="skip to this offset (seconds)")
    a.add_argument("--duration", type=float, help="process only this many seconds")
    a.add_argument("--keep-audio", action="store_true", help="keep the normalized 16k WAV")

    o = p.add_argument_group("output")
    o.add_argument("--srt", action="store_true", help="also write .srt subtitles")
    o.add_argument("--vtt", action="store_true", help="also write .vtt subtitles")
    o.add_argument("--json", action="store_true", help="also write .json with segments")
    o.add_argument("--pdf", action="store_true", help="also write a typeset .pdf")
    o.add_argument("--title", help="PDF title (default: derived from the filename)")
    o.add_argument("--subtitle", help="PDF subtitle (default: date parsed from the filename)")
    o.add_argument("--page-size", default="letter", help="PDF page size, e.g. letter or A4")
    o.add_argument("--font-size", type=float, default=11.5, help="PDF body size in points")
    o.add_argument("--no-txt", action="store_true", help="skip the plaintext .txt")
    o.add_argument("--txt-style", default="paragraphs", choices=["paragraphs", "lines", "raw"],
                   help="plaintext layout (default: paragraphs)")
    o.add_argument("--wrap", type=int, default=0, help="hard-wrap width, 0 = no wrapping")
    o.add_argument("--timestamps", action="store_true", help="prefix timestamps in .txt")
    o.add_argument("--overwrite", action="store_true", help="replace existing output files")
    o.add_argument("-q", "--quiet", action="store_true")
    o.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    global _QUIET
    args = build_parser().parse_args(argv)
    _QUIET = args.quiet

    formats = [] if args.no_txt else ["txt"]
    formats += [f for f in ("srt", "vtt", "json", "pdf") if getattr(args, f)]
    if not formats:
        print("nothing to write: --no-txt with no other format", file=sys.stderr)
        return 2

    files = expand_inputs(args.inputs, args.recursive)
    if not files:
        print("no media files found", file=sys.stderr)
        return 2

    try:
        tools = FFTools.discover()
    except TranscriberError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    failures = 0
    for i, f in enumerate(files, 1):
        if len(files) > 1:
            log(f"=== [{i}/{len(files)}] {f.name} ===")
        try:
            transcribe_file(
                f,
                outdir=args.outdir, formats=formats, backend=args.backend,
                model=args.model, language=args.language, task=args.task,
                device=args.device, normalize=args.normalize, denoise=args.denoise,
                lufs=args.lufs, true_peak=args.true_peak, lra=args.lra,
                start=args.start, duration=args.duration, keep_audio=args.keep_audio,
                txt_style=args.txt_style, wrap=args.wrap, timestamps=args.timestamps,
                title=args.title, subtitle=args.subtitle,
                page_size=args.page_size, font_size=args.font_size,
                initial_prompt=args.initial_prompt, threads=args.threads,
                overwrite=args.overwrite, tools=tools,
            )
        except TranscriberError as exc:
            failures += 1
            print(f"error [{f.name}]: {exc}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        except Exception as exc:  # noqa: BLE001 - one bad file must not end a batch
            failures += 1
            print(f"error [{f.name}]: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
            if os.environ.get("AV_TRANSCRIBER_DEBUG"):
                raise

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
