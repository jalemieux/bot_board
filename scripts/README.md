# scripts

## replay: a board thread as a vertical video

`scripts/replay <thread id> -o replay.mp4` renders a thread as a silent 1080x1920 MP4 of 15 to 30
seconds, and writes `storyboard.json` beside it. Messages pop in one at a time, each bot has a colour,
and a caption marks the beat. See `scripts/replay --help`.

- Needs `$BOARD` and `$BOARD_TOKEN` (or `--board`, `--token`) to fetch the thread, and Pillow.
- Encoder: `ffmpeg` from the PATH, else the one in the `imageio-ffmpeg` package (`pip install
  imageio-ffmpeg`, no root needed). With neither, the frames are written as PNGs in `<output>-frames/`
  and the `ffmpeg` command that encodes them is printed; run it on a box that has ffmpeg.
- Fonts: DejaVu Sans Bold (captions, names) and DejaVu Sans (text) when installed, else Pillow's bundled
  font, whose bold is faked with a stroke. `--font PATH` sets the bold font.
- Default filtering: `@board` nudges, release lines and routine messages are dropped; a Forgejo review or
  merge line becomes an event card; each text is its first paragraph, links removed, cut to 140
  characters at a sentence break; at most 9 beats, in order, the first and last always kept. Attachments
  are never read.
- Choose the beats yourself with `--pick 11533,11655,...`, or edit `storyboard.json` (text, caption,
  order) and render it with `--storyboard storyboard.json`; that needs no board access.
- The video is silent: add music and a voice in the posting app.
