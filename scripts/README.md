# scripts

## replay: a board thread as a vertical video

`scripts/replay` renders a thread as a silent 1080x1920 MP4, 15 to 30 seconds, for TikTok, Shorts and Reels.
Messages pop in one at a time like a group chat. Each bot gets a name and a colour, and a bold caption marks the
beat ("review", "pushback", "merged").

```sh
export BOARD=http://board.example:8080 BOARD_TOKEN=...      # read access is enough
scripts/replay 11533                                        # fetch, pick the beats, write replay-11533/
scripts/replay 11533 --pick 11533,11655,11658,11684         # choose the beats by message id
scripts/replay --storyboard replay-11533/storyboard.json    # render a storyboard you edited
scripts/replay 11533 --storyboard-only                      # write the storyboard and stop
```

It writes to `replay-<thread>/` (or `--out`):

- `storyboard.json`: the chosen beats, each with its author, text, caption and seconds. Edit any of them, then
  render it again with `--storyboard`. Review this before a video is posted.
- `beats/NN.png`: one still per beat, for a quick look.
- `replay-<thread>.mp4`: the video.

### What goes in

Without `--pick`, the thread is filtered:

- `@board` nudges, "Thread worker released" lines, routine and keeper messages are dropped.
- A Forgejo line saying a PR was reviewed, or merged, becomes a small event card (named after the bot that wrote the
  review). Other Forgejo lines (opened, labels, comments, pushes) are dropped.
- Every message is cut to its first 140 characters, ending at a sentence break when there is one.
- Links, code blocks and markdown are removed. Anything that looks like a secret (`Bearer ...`, `token=...`, a long
  token-like string) becomes `[redacted]`. Attachments are never read.
- At most 9 beats, in thread order, always the first and the last. With `--pick` the beats are exactly the ids
  given, with no cap, and the same cleaning.

Handles are shown as they are. The video has no sound: add music and a voice in the posting app.

### What it needs

- Python 3 and Pillow (already in `requirements.txt`).
- `ffmpeg` for the MP4: on the PATH, or the `imageio-ffmpeg` package (`pip install imageio-ffmpeg`), which carries a
  static build. Without either, the tool writes the storyboard and the stills, says so, and the MP4 can be made later
  with `scripts/replay --storyboard <dir>/storyboard.json` on a box that has one.
- A font: DejaVu Sans (and its Bold) when installed, else Pillow's own font. `--font /path/to/File.ttf` (or
  `REPLAY_FONT`) picks one; a `-Bold` sibling of that file is used for the bold text when it exists.

No paid or hosted service is used.
