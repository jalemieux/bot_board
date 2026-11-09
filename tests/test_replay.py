"""scripts/replay: which messages become beats, what is cleaned out of them, and the files it writes
(board thread 11936). No network and no ffmpeg: the thread is a fixture.
Run with python -m unittest discover -s tests."""
import importlib.machinery
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_loader("replay", importlib.machinery.SourceFileLoader("replay", str(ROOT / "scripts" / "replay")))
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def msg(i, author, body, akind="claude-code", **meta):
    return {"id": i, "author": author, "author_kind": akind, "body": body, "meta": meta}


def forgejo(i, body, event="pull_request"):
    return msg(i, "board", body, "cron", kind="forgejo", event=event)


THREAD = {"thread_id": 1, "messages": [
    msg(1, "hugo", "New board change to take. **What:** count a done reply, see http://forge.example/x?token=abc and `post_message`."),
    forgejo(2, "Forgejo: fleet opened PR #5 (WIP: x (#4)) http://forge.example/pulls/5"),
    forgejo(3, "Forgejo: fleet reviewed PR #5 (x) with comments http://forge.example/pulls/5\n> bot milo, board thread 1", "review"),
    msg(4, "board", "@kira this thread has not moved in 6 hours.", "cron", kind="nudge"),
    msg(5, "board", "Thread worker released by the board: @kira did not answer.", "cron", kind="thread_released"),
    msg(6, "routines", "Route each issue below.", "cron", routine=12),
    msg(7, "jade", "Send back PR #5: fix 1 first. The rest is fine and can wait for the next round of work."),
    msg(8, "kira", "That is already fixed in commit b0d507f. Please recheck."),
    forgejo(9, "Forgejo: jac merged PR #5 (x) http://forge.example/pulls/5"),
]}


class Filtering(unittest.TestCase):
    def beats(self, **kw):
        story = replay.build_storyboard(THREAD, **kw)
        return story, [b["id"] for b in story["beats"]]

    def test_noise_goes_and_reviews_and_merges_become_cards(self):
        story, ids = self.beats()
        self.assertEqual(ids, [1, 3, 7, 8, 9], "nudge, release, routine, an opened PR are dropped")
        cards = {b["id"]: b for b in story["beats"] if b["kind"] == "event"}
        self.assertEqual(cards[3]["text"], "milo reviewed PR #5", "the bot that wrote the review, not the shared account")
        self.assertEqual(cards[9]["text"], "jac merged PR #5")
        self.assertEqual(story["left_out"]["noise"], 4)

    def test_text_is_plain_and_short(self):
        story, _ = self.beats()
        first = story["beats"][0]["text"]
        self.assertNotIn("http", first)
        self.assertNotIn("token=abc", first)
        self.assertNotIn("**", first)
        self.assertIn("post_message", first, "underscores in names stay")
        self.assertTrue(all(len(b["text"]) <= replay.LIMIT + 1 for b in story["beats"]))

    def test_the_cut_ends_at_a_sentence_break_or_a_word(self):
        cut = replay.shorten("A short one. " + "x" * 10 + " " + "word " * 40, 80)
        self.assertTrue(cut.endswith("…") or cut.endswith("."))
        self.assertLessEqual(len(cut), 81)
        self.assertEqual(replay.shorten("Fits.", 140), "Fits.")
        self.assertEqual(replay.shorten("One sentence that is long enough to pass forty chars. And a second one that runs on past it.", 70),
                         "One sentence that is long enough to pass forty chars.")

    def test_secrets_never_reach_a_beat(self):
        body = ("Use Authorization: Bearer abcdefgh12345678 and password=hunter2 and "
                "ghp_" + "a1" * 20 + " here. ```\nSECRET=x\n```")
        out = replay.clean(body)
        for leak in ("abcdefgh12345678", "hunter2", "a1a1a1a1", "SECRET=x"):
            self.assertNotIn(leak, out)
        self.assertIn("[redacted]", out)

    def test_an_attachment_is_never_read(self):
        m = msg(1, "jac", "see the picture")
        m["image"] = {"url": "/api/messages/1/image"}
        m["attachments"] = [{"name": "secret.png"}]
        story = replay.build_storyboard({"thread_id": 1, "messages": [m]})
        self.assertNotIn("secret.png", json.dumps(story))
        self.assertNotIn("/api/messages", json.dumps(story))

    def test_at_most_nine_beats_in_order_with_the_first_and_last(self):
        many = {"thread_id": 2, "messages": [msg(i, "a" if i % 2 else "b", f"Message number {i}.") for i in range(1, 31)]}
        story = replay.build_storyboard(many)
        ids = [b["id"] for b in story["beats"]]
        self.assertEqual(len(ids), 9)
        self.assertEqual((ids[0], ids[-1]), (1, 30))
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(story["left_out"]["capped"], 21)

    def test_pick_keeps_exactly_those_in_thread_order(self):
        _, ids = self.beats(pick={8, 1, 9})
        self.assertEqual(ids, [1, 8, 9])
        with self.assertRaises(SystemExit):
            replay.build_storyboard(THREAD, pick={1, 99})

    def test_captions_mark_the_beats(self):
        story, _ = self.beats()
        caps = {b["id"]: b["caption"] for b in story["beats"]}
        self.assertEqual((caps[1], caps[3], caps[7], caps[8], caps[9]), ("the task", "review", "pushback", "answer", "merged"))

    def test_the_video_is_15_to_30_seconds(self):
        for n in (3, 7, 9, 14):
            self.assertTrue(15 <= n * replay.seconds_for(n) <= 30.1, n)


class Output(unittest.TestCase):
    def run_main(self, *args, ffmpeg=None):
        with tempfile.TemporaryDirectory() as tmp, patch.object(replay, "fetch", return_value=THREAD), \
                patch.object(replay, "find_ffmpeg", return_value=ffmpeg), patch.dict(os.environ, {"BOARD": "http://x"}):
            out = os.path.join(tmp, "o")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(replay.main(["1", "--out", out, *args]), 0)
            files = sorted(os.listdir(out)), sorted(os.listdir(os.path.join(out, "beats"))) if os.path.isdir(os.path.join(out, "beats")) else []
            with open(os.path.join(out, "storyboard.json")) as fh:
                story = json.load(fh)
            return files, story

    def test_storyboard_only_writes_just_the_storyboard(self):
        (top, stills), story = self.run_main("--storyboard-only")
        self.assertEqual((top, stills), (["storyboard.json"], []))
        self.assertEqual([b["id"] for b in story["beats"]], [1, 3, 7, 8, 9])

    def test_without_ffmpeg_it_writes_the_stills_and_says_so(self):
        (top, stills), _ = self.run_main()
        self.assertEqual(top, ["beats", "storyboard.json"])
        self.assertEqual(stills, ["01.png", "02.png", "03.png", "04.png", "05.png"])

    def test_a_still_is_1080_by_1920(self):
        from PIL import Image
        story = replay.build_storyboard(THREAD)
        with tempfile.TemporaryDirectory() as tmp, patch.object(replay, "find_ffmpeg", return_value=None):
            self.assertIsNone(replay.render(story, tmp))
            with Image.open(os.path.join(tmp, "beats", "05.png")) as im:
                self.assertEqual(im.size, (1080, 1920))

    def test_an_edited_storyboard_is_what_gets_rendered(self):
        story = replay.build_storyboard(THREAD)
        story["beats"][0]["text"] = "Edited by hand."
        with tempfile.TemporaryDirectory() as tmp, patch.object(replay, "find_ffmpeg", return_value=None):
            path = os.path.join(tmp, "storyboard.json")
            with open(path, "w") as fh:
                json.dump(story, fh)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(replay.main(["--storyboard", path]), 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "beats", "01.png")))


if __name__ == "__main__":
    unittest.main()
