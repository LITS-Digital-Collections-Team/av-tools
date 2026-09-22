===============================================================================
av-transcriber 1.0.0
Rip, normalize, and transcribe audio from any audio or video file.
===============================================================================

One command in, a plaintext transcript out. Everything runs locally: no API
keys, no accounts, no network calls at runtime beyond the one-time model
download that Whisper performs on first use.

    media file --ffprobe--> inspect --ffmpeg--> 16 kHz mono PCM --whisper--> .txt
                                        |                                   .pdf
                                  EBU R128 loudnorm                         .srt
                                                                            .vtt
                                                                            .json


-------------------------------------------------------------------------------
CONTENTS
-------------------------------------------------------------------------------

  1.  Why the extra steps
  2.  Requirements
  3.  Installation
  4.  Quick start
  5.  Worked example
  6.  Command reference
  7.  Choosing a model
  8.  Choosing a normalization mode
  9.  Output formats
  10. PDF output
  11. Batch processing
  12. Library use
  13. Environment variables
  14. Exit codes
  15. How it works, stage by stage
  16. Troubleshooting
  17. Privacy and data handling
  18. Repository layout
  19. License


-------------------------------------------------------------------------------
1. WHY THE EXTRA STEPS
-------------------------------------------------------------------------------

Handing a video file straight to Whisper works, but badly, for three reasons
this tool addresses.

NORMALIZATION MATTERS. Whisper degrades on quiet or wildly uneven audio, which
is exactly what lectern mics and handheld recorders produce. A two-pass EBU
R128 `loudnorm` measures the whole program first, then applies a single linear
correction, so a quiet ceremony and a hot interview both land at -16 LUFS. The
sample recording shipped in examples/ measures -33.4 LUFS on the way in; that
is the common case, not a contrived one.

16 KHZ MONO UP FRONT. Whisper resamples to 16 kHz mono internally no matter
what you give it. Doing it once in ffmpeg avoids a second decode, shrinks the
intermediate file to roughly 115 MB per hour, and lets the samples be handed to
Whisper as an in-memory array instead of a file path.

FFMPEG IS LOCATED, NOT ASSUMED. Each candidate binary is actually executed
before use. A Homebrew ffmpeg whose linked codec libraries have been upgraded
out from under it is on PATH and looks fine, but aborts on every invocation;
the tool detects that and moves on to a working build.


-------------------------------------------------------------------------------
2. REQUIREMENTS
-------------------------------------------------------------------------------

  * Python 3.9 or newer.
  * ffmpeg and ffprobe. Either a system install or the pip package
    `static-ffmpeg`, which ships self-contained builds.
  * One Whisper backend:
      - openai-whisper (Python; pulls in PyTorch, which is most of the
        install size), or
      - whisper.cpp, providing a `whisper-cli` binary (Metal-accelerated on
        Apple silicon, much smaller install).
  * Optional, only for --pdf: WeasyPrint plus its native Pango/GLib libraries.

Tested on macOS (Apple silicon and Intel) and Linux. The bin/av-transcribe
launcher is bash; on Windows, call av_transcriber.py directly with a Python
interpreter that has Whisper installed.


-------------------------------------------------------------------------------
3. INSTALLATION
-------------------------------------------------------------------------------

Self-contained project venv (recommended):

    ./bin/av-transcribe --setup

That creates .venv/ next to the tool and installs requirements.txt into it.
Expect a few GB, almost all of it PyTorch.

Into an environment you already have:

    pip install openai-whisper static-ffmpeg

The whisper.cpp backend instead, if you want a small install or GPU
acceleration on Apple silicon:

    brew install whisper-cpp
    mkdir -p ~/.cache/whisper-cpp
    curl -L -o ~/.cache/whisper-cpp/ggml-large-v3-turbo.bin \
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

PDF output, which is optional:

    pip install weasyprint
    brew install pango                              # macOS
    apt install libpango-1.0-0 libpangoft2-1.0-0    # Debian/Ubuntu

HOW THE LAUNCHER FINDS AN INTERPRETER

bin/av-transcribe looks for a Python that can `import whisper`, in this order:

    1. $AV_TRANSCRIBER_PYTHON
    2. a .venv/ beside the tool, or in any parent directory (up to 5 levels)
    3. python3 on PATH

If none of them has Whisper it uses the best interpreter it found anyway, so
the Python layer can print its own install instructions or fall through to the
whisper.cpp backend.

The launcher also extends DYLD_FALLBACK_LIBRARY_PATH on macOS so WeasyPrint can
find Homebrew's Pango. Use the launcher rather than calling av_transcriber.py
directly when producing PDFs.

PUTTING IT ON YOUR PATH

    ln -s "$PWD/bin/av-transcribe" /usr/local/bin/av-transcribe

The launcher resolves symlinks, so the tool can live anywhere.


-------------------------------------------------------------------------------
4. QUICK START
-------------------------------------------------------------------------------

    av-transcribe talk.mp4
        Writes talk.txt next to talk.mp4.

    av-transcribe --pdf 2024-09-28_Board-Meeting.mov
        Writes .txt plus a typeset .pdf, with the title and date pulled out of
        the filename.

    av-transcribe -m medium --srt --json lecture.mov -o ./out
        Bigger model, subtitles and structured segments, into ./out.

    av-transcribe --start 60 --duration 120 -m tiny long.mp4
        Transcribe two minutes starting at 1:00 with the fastest model. Do this
        before committing to a multi-hour run.

    av-transcribe ./recordings -r -m small -o ./transcripts
        Every media file under ./recordings, recursively.


-------------------------------------------------------------------------------
5. WORKED EXAMPLE
-------------------------------------------------------------------------------

examples/ holds a complete input and its real output, generated by the scripts
in that directory rather than written by hand.

INPUT

    examples/2026-03-14_Quarterly-Planning-Review.m4a

A 46-second synthesized meeting excerpt, mono AAC at 32 kbps, deliberately
encoded 17 dB down so that it measures about -33 LUFS -- roughly what a phone
at the back of a room captures. It is synthetic speech so that it can be
redistributed with no consent or rights questions; examples/sample-script.txt
is the text it was read from.

COMMAND

    ./bin/av-transcribe examples/2026-03-14_Quarterly-Planning-Review.m4a \
      -o examples/output -m small -l en --srt --vtt --json --pdf

CONSOLE OUTPUT

    [av-transcriber] ffmpeg:  .../static_ffmpeg (no libsoxr, using swr)
    [av-transcriber] ffprobe: .../static_ffprobe
    [av-transcriber] input: 2026-03-14_Quarterly-Planning-Review.m4a
                            (audio, 0:46, audio=aac 22050Hz x1)
    [av-transcriber] normalizing: EBU R128 two-pass (pass 1/2, measuring)
    [av-transcriber] measured: I=-33.43 LUFS  TP=-18.00 dBTP  LRA=1.60
                               thresh=-43.51
    [av-transcriber] normalizing: pass 2/2, applying
    [av-transcriber] audio ready: ....16k.wav (1.5 MB, 16000 Hz mono)
    [av-transcriber] audio stage done in 0:02
    [av-transcriber] loading whisper model 'small' on cpu
    [av-transcriber] transcribed 14 segments in 0:04 (10.4x realtime),
                     language=en
    [av-transcriber] wrote .../2026-03-14_Quarterly-Planning-Review.txt
    ...

Note the measured loudness: -33.43 LUFS in, -16 LUFS out. All progress goes to
stderr, so stdout stays clean for piping.

FILES PRODUCED (all committed under examples/output/)

    2026-03-14_Quarterly-Planning-Review.txt              default, paragraphs
    2026-03-14_Quarterly-Planning-Review.timestamps.txt   --timestamps
    2026-03-14_Quarterly-Planning-Review.lines.txt        --txt-style lines
    2026-03-14_Quarterly-Planning-Review.srt              subtitles
    2026-03-14_Quarterly-Planning-Review.vtt              web subtitles
    2026-03-14_Quarterly-Planning-Review.json             segments + metadata
    2026-03-14_Quarterly-Planning-Review.pdf              typeset document

.txt (default, paragraphs style) -- opening:

    Good morning, and thanks for joining the quarterly planning review. I
    want to start with the archive digitization project. We finished the
    pilot last month. 1400 reels were inspected, and about 900 of them are
    in good enough condition to run through the transfer station without
    any baking. ...

.txt with --txt-style lines --timestamps -- one Whisper segment per line:

    [0:00] Good morning, and thanks for joining the quarterly planning review.
    [0:04] I want to start with the archive digitization project.
    [0:07] We finished the pilot last month.
    [0:09] 1400 reels were inspected, and about 900 of them are in good
           enough condition to run
    [0:14] through the transfer station without any baking.

.srt:

    1
    00:00:00,000 --> 00:00:03,560
    Good morning, and thanks for joining the quarterly planning review.

    2
    00:00:03,560 --> 00:00:06,880
    I want to start with the archive digitization project.

.vtt:

    WEBVTT

    00:00:00.000 --> 00:00:03.560
    Good morning, and thanks for joining the quarterly planning review.

.json:

    {
      "source": "2026-03-14_Quarterly-Planning-Review.m4a",
      "duration": 45.538005,
      "language": "en",
      "backend": "openai-whisper",
      "model": "small",
      "meta": { "device": "cpu" },
      "text": "Good morning, and thanks for joining the quarterly ...",
      "segments": [
        { "start": 0.0, "end": 3.56,
          "text": "Good morning, and thanks for joining the quarterly
                   planning review." },
        ...
      ]
    }

The committed .json has its "source" field rewritten to the bare filename. A
real run records the absolute path of the input.

REGENERATING

    cd examples
    ./make-sample-input.sh     # rebuild the .m4a from sample-script.txt (macOS)
    ./make-sample-output.sh    # rebuild everything under output/

Run make-sample-output.sh after any change that affects rendering, so the
committed examples never drift from what the tool actually produces. Exact
wording may vary slightly between Whisper releases.


-------------------------------------------------------------------------------
6. COMMAND REFERENCE
-------------------------------------------------------------------------------

    av-transcribe [options] INPUT [INPUT ...]

INPUT is a media file or a directory. Directories are scanned for known audio
and video extensions; add -r to recurse.

POSITIONAL AND GENERAL

    INPUT...                media file(s) or director(ies)
    -o, --outdir DIR        output directory (default: alongside each input)
    -r, --recursive         recurse into input directories
    -q, --quiet             suppress progress logging on stderr
    --version               print the version and exit
    -h, --help              print usage and exit

TRANSCRIPTION

    -m, --model NAME        tiny | base | small | medium | large-v3 | turbo
                            (default: small). For whisper.cpp this may also be
                            a path to a ggml-*.bin file.
    --backend NAME          auto | whisper | whisper-cpp (default: auto).
                            auto prefers the Python backend if importable,
                            then looks for whisper-cli / whisper-cpp.
    -l, --language CODE     ISO 639-1 code, e.g. en. Omit to auto-detect.
                            Setting it is faster and avoids misdetection on
                            music-heavy or silent intros.
    --task NAME             transcribe (default) | translate. translate
                            produces English output from any source language.
    --device NAME           cpu | cuda | auto (Python backend only).
                            Default: cuda when available, otherwise cpu.
    --threads N             whisper.cpp thread count (default: CPU count - 2)
    --prompt TEXT           initial prompt fed to the decoder to bias spelling
                            of proper nouns and jargon. Example:
                            --prompt "Attendees: Anouk Rasheed, Ji-woo Park.
                            Topics: LUFS, ffprobe, EBU R128."

AUDIO

    --normalize MODE        ebu (default) | fast | peak | none. See section 8.
    --denoise               80 Hz highpass, 7.5 kHz lowpass, and afftdn
                            spectral denoise, applied before measurement.
                            For hall reverb, HVAC rumble, and handling noise.
    --lufs N                target integrated loudness (default: -16.0)
    --true-peak N           target true peak in dBTP (default: -1.5)
    --lra N                 target loudness range (default: 11.0)
    --start SECONDS         skip to this offset before processing
    --duration SECONDS      process only this many seconds
    --keep-audio            keep the normalized 16 kHz WAV as STEM.16k.wav
                            next to the outputs, instead of discarding it

OUTPUT

    --srt                   also write .srt subtitles
    --vtt                   also write .vtt subtitles
    --json                  also write .json with per-segment timings
    --pdf                   also write a typeset .pdf (needs WeasyPrint)
    --no-txt                skip the plaintext .txt
    --txt-style STYLE       paragraphs (default) | lines | raw. See section 9.
    --wrap N                hard-wrap the .txt at N columns (0 = no
                            wrapping). Ignored by --txt-style lines.
    --timestamps            prefix [h:mm:ss] markers in the .txt and .pdf.
                            Ignored by --txt-style raw.
    --title TEXT            PDF title (default: derived from the filename)
    --subtitle TEXT         PDF subtitle (default: date parsed from filename)
    --page-size NAME        PDF page size: letter (default), A4, ...
    --font-size N           PDF body size in points (default: 11.5)
    --overwrite             replace existing output files

Without --overwrite, the run aborts before transcription if any output file
already exists -- the check happens up front, so you are not told after
twenty minutes of decoding.


-------------------------------------------------------------------------------
7. CHOOSING A MODEL
-------------------------------------------------------------------------------

Model choice is the main speed/quality lever. Rough throughput on a 10-core
Apple silicon CPU with the Python backend:

    tiny        ~40x realtime    sanity checks only
    base        ~20x             rough notes, clean audio
    small       ~8x              the default; good for clear speech
    medium      ~2.5x            noticeably better on names and accents
    large-v3    ~1x              best accuracy, slow on CPU
    turbo       ~6x              large-v3 quality at much lower cost,
                                 transcription only (no translate)

The whisper.cpp backend on Apple silicon is several times faster than these
figures because it uses Metal.

Work the slice-first way on anything long: run --start / --duration with
-m tiny to confirm the audio is what you think it is, then commit to the real
model.


-------------------------------------------------------------------------------
8. CHOOSING A NORMALIZATION MODE
-------------------------------------------------------------------------------

    ebu     Two-pass EBU R128 loudnorm (default). Measures the whole program,
            then applies one linear correction. The only mode that treats the
            recording as a whole. Costs a second decode of the input.

    fast    Single-pass loudnorm. One decode, but it adapts as it goes, so it
            can pump on sparse material (long pauses, applause). Reasonable
            for large batches where throughput matters more.

    peak    dynaudnorm. Evens out level without targeting a loudness standard.
            Use when you want consistency but not a specific LUFS value.

    none    Pass the audio through untouched. Use when the source is already
            mastered, or when you are debugging something else.

--lufs, --true-peak, and --lra apply to ebu and fast. The defaults (-16 LUFS,
-1.5 dBTP, LRA 11) are the spoken-word/podcast convention. Broadcast delivery
uses -23 LUFS; pass --lufs -23 if that is what you need.


-------------------------------------------------------------------------------
9. OUTPUT FORMATS
-------------------------------------------------------------------------------

.txt is written unless you pass --no-txt. Everything else is opt-in. Outputs
are named after the input stem: talk.mp4 produces talk.txt, talk.srt, and so
on, in --outdir or beside the input.

TXT STYLES

    paragraphs  (default) Whisper emits 5-30 second segments with no notion of
                a paragraph. This style groups them: it breaks on a silence of
                2 seconds or more that also lands on a sentence boundary, or
                once a paragraph has run past 45 seconds. The result reads
                like prose.

    lines       One Whisper segment per line, verbatim. Best when you intend
                to post-process the text, or want to see exactly where the
                model drew its boundaries.

    raw         The whole transcript as one unbroken block.

Not every option applies to every style:

    style       --wrap    --timestamps
    paragraphs  yes       yes, at the head of each paragraph
    lines       no        yes, at the head of each line
    raw         yes       no

SRT AND VTT

One cue per Whisper segment, with the model's own timings. No re-segmentation
to a reading-speed target, so long segments stay long -- fine for search and
indexing, worth a pass by hand for broadcast captioning.

JSON

    source      absolute path of the input file
    duration    seconds, from ffprobe
    language    detected or forced language code
    backend     "openai-whisper" or "whisper.cpp"
    model       model name or ggml filename
    meta        backend-specific detail, e.g. {"device": "cpu"}
    text        the full transcript as one string
    segments    [{start, end, text}, ...], times in seconds to 3 decimals

This is the format to build on. It is stable and carries everything the other
renderers are derived from.


-------------------------------------------------------------------------------
10. PDF OUTPUT
-------------------------------------------------------------------------------

--pdf renders a typeset document rather than a text dump: serif body at a
readable measure, ragged-right with automatic hyphenation, orphan and widow
control, and page numbers. Hyphenation is on because justification without a
good hyphenator opens rivers of whitespace.

Title and subtitle are derived from the filename. A stem of the form
YYYY-MM-DD_Some-Title or YYYYMMDD Some Title is split, so

    2024-09-28_Innovation-Center-Groundbreaking.mp4

becomes the title "Innovation Center Groundbreaking" with the subtitle
"September 28, 2024". Underscores and hyphens become spaces. If the leading
digits are not a real date, the stem is left intact.

Override with --title and --subtitle. Pass --subtitle "" to suppress the
subtitle entirely. Adjust the setting with --page-size and --font-size.
Timestamps are omitted unless you pass --timestamps, which renders them as
small gray markers at the head of each paragraph.

--pdf needs WeasyPrint and its native Pango/GLib libraries. Invoke the tool
through bin/av-transcribe, not by running av_transcriber.py directly: on macOS
the launcher sets DYLD_FALLBACK_LIBRARY_PATH so those libraries can be found,
and that has to happen before the interpreter starts.


-------------------------------------------------------------------------------
11. BATCH PROCESSING
-------------------------------------------------------------------------------

Pass directories instead of files:

    av-transcribe ./recordings -r -m small -o ./transcripts

Recognized extensions:

    audio   .wav .mp3 .m4a .aac .flac .ogg .oga .opus .wma .aif .aiff
            .alac .caf .amr .mka
    video   .mp4 .mov .m4v .mkv .avi .webm .flv .wmv .mpg .mpeg .ts
            .mts .m2ts .3gp .ogv

Files are processed one at a time, in sorted order, with a progress header per
file. A failure on one file is reported and the batch continues; the exit code
is 1 if anything failed. Ctrl-C stops the whole run.

Without --overwrite, files whose outputs already exist are skipped with an
error, which makes re-running an interrupted batch cheap.

For long batches, consider --normalize fast (one decode instead of two) and
--backend whisper-cpp.


-------------------------------------------------------------------------------
12. LIBRARY USE
-------------------------------------------------------------------------------

    from av_transcriber import transcribe_file

    written = transcribe_file(
        "interview.mov",
        outdir="transcripts",
        formats=("txt", "pdf", "srt"),
        model="medium",
        language="en",
        normalize="ebu",
    )
    print(written["txt"])

transcribe_file returns a dict mapping each format name to the Path written,
plus a "wav" key when keep_audio=True. Its keyword arguments mirror the CLI
flags. It raises TranscriberError with a readable message on any expected
failure.

Individual stages are importable if you only want part of the pipeline:

    FFTools.discover()          locate working ffmpeg/ffprobe
    probe(tools, path)          -> MediaInfo
    extract_audio(...)          -> normalized 16 kHz mono WAV
    transcribe_whisper_py(...)  -> Transcript
    transcribe_whisper_cpp(...) -> Transcript
    render_txt / render_srt / render_vtt / render_json / render_pdf
    to_paragraphs(segments)     the paragraph grouper, usable on its own
    derive_title(stem)          -> (title, subtitle)

Transcript carries .segments (a list of Segment with start, end, text),
.language, .backend, .model, .meta, and a .text property.


-------------------------------------------------------------------------------
13. ENVIRONMENT VARIABLES
-------------------------------------------------------------------------------

    FFMPEG_BIN              Force a specific ffmpeg binary.
    FFPROBE_BIN             Force a specific ffprobe binary.
    AV_TRANSCRIBER_PYTHON   Force the interpreter used by bin/av-transcribe.
    WHISPER_CPP_MODEL       Path to a ggml-*.bin for the whisper.cpp backend.
                            An error is raised if it does not point at a file.
    AV_TRANSCRIBER_DEBUG    Set to any value to re-raise unexpected exceptions
                            with a full traceback instead of a one-line error.

None of these are required. The tool reads no configuration file and no
credentials of any kind.


-------------------------------------------------------------------------------
14. EXIT CODES
-------------------------------------------------------------------------------

    0    everything succeeded
    1    at least one input failed, or setup failed (no working ffmpeg, etc.)
    2    nothing to do (no media files matched, or --no-txt with no other
         output format requested)
    130  interrupted with Ctrl-C


-------------------------------------------------------------------------------
15. HOW IT WORKS, STAGE BY STAGE
-------------------------------------------------------------------------------

1. PROBE. ffprobe reports duration, codec, sample rate, channels, and whether
   real audio and video streams exist. Cover art embedded in an MP3 shows up
   as a video stream, so mjpeg/png/bmp/gif video streams are not counted as
   video.

2. MEASURE (ebu mode only). ffmpeg runs the audio through loudnorm in
   analysis mode and prints a JSON blob of measured loudness, true peak,
   loudness range, and threshold.

3. EXTRACT. ffmpeg decodes the audio stream, applies the optional denoise
   chain, applies loudnorm with the measured values so the correction is
   linear, resamples to 16 kHz mono, and writes 16-bit PCM. libsoxr is used
   when the build has it and the built-in resampler otherwise -- asking for a
   resampler that was compiled out is a hard filter-graph error, so the
   presence of libsoxr is detected from the ffmpeg version banner.

4. TRANSCRIBE. The WAV is read directly into a float32 array and handed to
   Whisper. This matters: Whisper's own loader would shell out to whatever
   ffmpeg is on PATH and decode the file a second time, which both wastes work
   and reintroduces the broken-ffmpeg problem the tool just routed around.
   The whisper.cpp backend gets the WAV path and its JSON output is parsed
   back into the same Segment structure.

5. RENDER. Each requested format is written from the same Transcript object.

Intermediate audio goes to a temporary directory that is removed on exit,
including on failure, unless --keep-audio is given.


-------------------------------------------------------------------------------
16. TROUBLESHOOTING
-------------------------------------------------------------------------------

"No working ffmpeg found"
    Nothing on PATH ran successfully. Install one with `brew install ffmpeg`
    or `pip install static-ffmpeg`, or set FFMPEG_BIN and FFPROBE_BIN to
    working binaries.

"Library not loaded: libx265.*.dylib" from ffmpeg
    A Homebrew ffmpeg linked against codec libraries that have since been
    upgraded. The tool routes around it automatically by finding another
    build. To repair it: `brew reinstall ffmpeg`.

"Requested resampling engine is unavailable"
    The ffmpeg build lacks libsoxr. Handled automatically by falling back to
    the built-in resampler; the log line says "(no libsoxr, using swr)".

"No Whisper backend available"
    Neither the Python package nor whisper-cli is importable or on PATH.
    See section 3. If you installed openai-whisper into a venv, make sure the
    launcher can find it -- set AV_TRANSCRIBER_PYTHON to that venv's python3.

"No whisper.cpp model 'ggml-NAME.bin' found"
    Download one into ~/.cache/whisper-cpp/, or set WHISPER_CPP_MODEL:

        mkdir -p ~/.cache/whisper-cpp
        curl -L -o ~/.cache/whisper-cpp/ggml-large-v3-turbo.bin \
          https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

"WeasyPrint could not load its native libraries"
    Install Pango: `brew install pango`, or
    `apt install libpango-1.0-0 libpangoft2-1.0-0`. On macOS those live
    outside the dynamic loader's search path, so run the tool through
    bin/av-transcribe, which extends DYLD_FALLBACK_LIBRARY_PATH for you.

"already exists: NAME (use --overwrite to replace)"
    Working as intended -- the check runs before transcription so you do not
    lose a long run to a name collision. Pass --overwrite or choose a
    different --outdir.

"HAS NO AUDIO STREAM TO TRANSCRIBE"
    ffprobe found no audio in the container. Confirm with
    `ffprobe -show_streams FILE`.

Transcript is garbled, or in the wrong language
    Pass -l en (or the right code) instead of relying on auto-detection; a
    musical or silent opening throws detection off. Try --denoise for noisy
    rooms, and a larger --model. Use --prompt to supply proper nouns.

Apple silicon GPU is not being used
    The Python backend deliberately runs on CPU: Whisper's decoder hits
    unimplemented MPS operations. For GPU acceleration use
    --backend whisper-cpp, which uses Metal.

Very long files
    Memory use is dominated by the model, not the audio, so hours-long inputs
    are fine. Slice with --start / --duration if you only need part.


-------------------------------------------------------------------------------
17. PRIVACY AND DATA HANDLING
-------------------------------------------------------------------------------

Transcription happens entirely on the local machine. The tool contains no API
keys, reads no credentials, and makes no network requests of its own. The one
exception is indirect: the first time the Python backend is asked for a model
it has not seen, openai-whisper downloads the weights from OpenAI's CDN and
caches them in ~/.cache/whisper. After that, runs are fully offline. The
whisper.cpp backend never downloads anything -- you supply the .bin yourself.

Input audio, intermediate WAVs, and transcripts stay on disk where you put
them. Intermediate audio is written to a temporary directory and deleted on
exit unless --keep-audio is given.

Note that the .json output records the absolute path of the input file, which
can disclose a directory structure if you publish it.


-------------------------------------------------------------------------------
18. REPOSITORY LAYOUT
-------------------------------------------------------------------------------

    av_transcriber.py          the whole implementation, CLI and library
    bin/av-transcribe          launcher: finds an interpreter, fixes dyld path
    requirements.txt           pip dependencies
    README.txt                 this file
    README.md                  short quick-start, points here
    examples/
      sample-script.txt        the text the sample audio was read from
      2026-03-14_...m4a        sample input (synthesized, quiet on purpose)
      make-sample-input.sh     regenerate the .m4a
      make-sample-output.sh    regenerate everything under output/
      output/                  real generated output in every format


-------------------------------------------------------------------------------
19. LICENSE
-------------------------------------------------------------------------------

No license has been chosen yet. Add a LICENSE file before distributing:
without one, default copyright applies and recipients have no right to use or
redistribute the code. MIT or Apache-2.0 are the usual choices for a tool like
this.

Note that the dependencies carry their own terms -- openai-whisper is MIT,
whisper.cpp is MIT, ffmpeg is LGPL or GPL depending on the build, and
WeasyPrint is BSD-3-Clause.
