Example input and output for av-transcriber.

Copyright (C) 2026 Patrick R. Wallace, Hamilton College.
This file and the example material in this directory are released under the
GNU Free Documentation License, Version 1.3 or later, with no Invariant
Sections, no Front-Cover Texts, and no Back-Cover Texts. The .sh scripts here
are under the GNU GPL, version 3 or later. See ../LICENSE.txt.

Everything in output/ was produced by actually running the tool, via
make-sample-output.sh -- none of it is hand-written. See section 5 of the
top-level README.txt for the full walkthrough.

  sample-script.txt
      The text the sample audio was read from.

  2026-03-14_Quarterly-Planning-Review.m4a
      The sample input: 46 seconds of synthesized meeting talk, mono AAC at
      32 kbps. Synthesized rather than recorded so it can be redistributed
      freely. Deliberately encoded 17 dB down, so it measures about
      -33 LUFS -- roughly a phone at the back of a room, which gives
      `--normalize ebu` something real to correct.

  make-sample-input.sh
      Rebuilds the .m4a from sample-script.txt. macOS only, because it uses
      the `say` command for text-to-speech. On Linux or WSL, substitute
      espeak-ng and keep the rest of the pipeline identical:
          sudo apt install -y espeak-ng
          espeak-ng -f sample-script.txt -w /tmp/raw.wav
      then run the same ffmpeg command the script uses on /tmp/raw.wav.
      You only need this to regenerate the audio; the committed sample
      works on every platform.

  make-sample-output.sh
      Rebuilds everything under output/. Run it after any change that
      affects rendering, so the committed examples stay honest.

  output/
      *.txt              default paragraph style
      *.timestamps.txt   --timestamps
      *.lines.txt        --txt-style lines --timestamps
      *.srt              subtitles
      *.vtt              web subtitles
      *.json             segments and metadata
      *.pdf              typeset document (--pdf)

The "source" field in the committed .json is rewritten to the bare filename;
a real run records the absolute path of the input.

Exact wording can shift slightly between Whisper releases, so a regenerated
transcript may not be byte-identical to the committed one.
