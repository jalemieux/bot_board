"""scripts/replay: which messages become beats, what is cleaned out of them, and how a long thread is
fetched (board thread 11936). No network, no Pillow, no ffmpeg: the thread is a fixture.
Run with python -m unittest discover -s tests. The fixture and several cases follow seth's tests."""
import importlib.machinery
import importlib.util
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_loader("replay", importlib.machinery.SourceFileLoader("replay", str(ROOT / "scripts" / "replay")))
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def msg(i, author, body, akind="claude-code", **meta):
    return {"id": i, "author": author, "author_kind": akind, "body": body, "meta": meta}


def forgejo(i, body):
    return msg(i, "board", body, "cron", kind="forgejo")


THREAD = [
    msg(1, "hugo", "New board change to take. See http://forge.example/x?token=abc and `post_message`."),
    forgejo(2, "Forgejo: fleet opened PR #5 (x (#4)) http://forge.example/pulls/5"),
    msg(3, "milo", "Reviewed PR #5: good to merge."),
    forgejo(4, "Forgejo: fleet reviewed PR #5 (x) with comments http://forge.example/pulls/5\n> bot milo, board thread 1"),
    msg(5, "board", "@kira this thread has not moved in 6 hours.", "cron", kind="nudge"),
    msg(6, "board", "Thread worker released by the board: @kira did not answer.", "cron", kind="thread_released"),
    msg(7, "routines", "Route each issue below.", "cron", routine=12),
    msg(8, "jade", "Send back PR #5: fix 1 first. The rest is fine."),
    msg(9, "kira", "That is already fixed in commit b0d507f. Please recheck."),
    forgejo(10, "Forgejo: jac merged PR #5 (x) http://forge.example/pulls/5"),
    msg(11, "kira", "Merged: PR #5 closed #4."),
]


class Cleaning(unittest.TestCase):
    def test_secrets_never_reach_a_beat(self):
        body = ("Use Authorization: Bearer abcdefgh12345678 and password=hunter2, ghp_" + "a1" * 20 + ", "
                "FORGEJO_TOKEN=abc12345 and " + "0123456789abcdef" * 2 + " here.")
        out = replay.clean(body)
        for leak in ("abcdefgh12345678", "hunter2", "a1a1a1a1", "abc12345", "0123456789abcdef"):
            self.assertNotIn(leak, out)
        self.assertIn("[redacted]", out)

    def test_a_code_fence_is_never_the_beat(self):
        self.assertEqual(replay.clean("```\nexport BOARD_TOKEN=abc123def456\n```\n\nThen run it."), "Then run it.")
        self.assertEqual(replay.clean("Run this: ```\nexport X_TOKEN=abc123\n"), "Run this:".rstrip(":"))

    def test_names_keep_their_underscores_and_angle_brackets(self):
        out = replay.clean("`post_message` in app/db.py asks `needs: <human>` for snake_case_name")
        self.assertEqual(out, "post_message in app/db.py asks needs: <human> for snake_case_name")
        self.assertEqual(replay.clean("_emphasis_ and\n> a quote"), "emphasis and a quote")

    def test_links_and_markdown_go(self):
        out = replay.clean("**What:** see http://forge.example/x?a=1 now")
        self.assertEqual(out, "What: see now")

    def test_the_cut_ends_at_a_sentence_break_or_a_word(self):
        self.assertEqual(replay.cut("Fits."), "Fits.")
        text = "One sentence that is long enough to pass forty chars. And a second one that runs on past it. " * 3
        self.assertEqual(replay.cut(text, 100), "One sentence that is long enough to pass forty chars. And a second one that runs on past it.")
        self.assertTrue(replay.cut("word " * 60, 50).endswith("…"))


class Storyboard(unittest.TestCase):
    def ids(self, **kw):
        return [b["id"] for b in replay.storyboard(THREAD, **kw)]

    def test_noise_goes_and_the_story_ends_on_the_merge(self):
        story = replay.storyboard(THREAD, None)
        self.assertEqual([b["id"] for b in story], [1, 3, 8, 9, 10], "nudge, release, routine, an opened PR, the duplicate review card, and what follows the merge are dropped")
        self.assertEqual(story[-1]["text"], "jac merged PR #5")
        self.assertEqual(story[-1]["caption"], "merged")

    def test_the_review_card_stays_when_no_message_of_that_author_precedes_it(self):
        thread = [m for m in THREAD if m["id"] != 3]
        story = replay.storyboard(thread, None)
        self.assertIn("milo reviewed PR #5", [b["text"] for b in story], "the bot is read from the quoted signature")

    def test_a_keeper_or_cron_author_is_noise(self):
        thread = THREAD + [msg(12, "keeper-box", "alive", "keeper")]
        self.assertNotIn(12, [b["id"] for b in replay.storyboard(thread, None)])

    def test_at_most_nine_beats_in_order_with_the_first_and_last(self):
        many = [msg(i, "a" if i % 2 else "b", f"Message number {i}.") for i in range(1, 31)]
        ids = [b["id"] for b in replay.storyboard(many, None)]
        self.assertEqual((len(ids), ids[0], ids[-1]), (9, 1, 30))
        self.assertEqual(ids, sorted(ids))

    def test_pick_keeps_those_in_the_given_order_and_names_a_stranger(self):
        self.assertEqual(self.ids(pick=[9, 1, 10]), [9, 1, 10])
        with self.assertRaises(SystemExit) as raised:
            replay.storyboard(THREAD, [1, 99])
        self.assertIn("99", str(raised.exception))

    def test_a_beat_has_no_link_and_no_attachment_content(self):
        m = msg(1, "jac", "see the picture")
        m["attachments"] = [{"name": "secret.png"}]
        m["image"] = {"url": "/api/messages/1/image"}
        self.assertNotIn("secret.png", json.dumps(replay.storyboard([m], None)))


class Fetching(unittest.TestCase):
    def page(self, messages, more=False, next_since=None):
        body = io.BytesIO(json.dumps({"messages": messages, "has_more": more, "next_since": next_since}).encode())
        body.__enter__ = lambda s: s
        body.__exit__ = lambda s, *a: None
        return body

    def test_a_long_thread_is_read_to_its_end(self):
        pages = [self.page([{"id": 1}, {"id": 2}], True, 2), self.page([{"id": 3}, {"id": 4}], False)]
        urls = []

        def fake(req, timeout=0):
            urls.append(req.full_url)
            return pages.pop(0)
        with patch.object(replay.urllib.request, "urlopen", fake):
            out = replay.fetch("http://b", "t", 7)
        self.assertEqual([m["id"] for m in out], [1, 2, 3, 4])
        self.assertEqual(urls, ["http://b/api/threads/7", "http://b/api/threads/7?since=2"])

    def test_an_error_from_the_board_is_one_line(self):
        def fake(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)
        with patch.object(replay.urllib.request, "urlopen", fake), self.assertRaises(SystemExit) as raised:
            replay.fetch("http://b", "t", 7)
        self.assertEqual(str(raised.exception), "replay: the board answered 404 for thread 7")


if __name__ == "__main__":
    unittest.main()
