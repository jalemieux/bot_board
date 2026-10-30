"""The check of the shipped board-daily-care routine (docs/routines/board-daily-care/check):
what it puts on the moderator's agenda, when it wakes a bot and when it stays quiet. It is
run as the runner runs it, as a script with BOARD and ROUTINE_WATERMARK in its environment,
against a small HTTP server standing in for the board's /api/status and /api/fleet."""
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "docs" / "routines" / "board-daily-care" / "check"

ALERT = {"text": "@cleo on jacs-m5: logged out of claude since 2026-10-30T20:11:17Z: run claude, then /login",
         "href": "/fleet#bot-cleo", "where": "fleet", "key": "5abd0f14ac8d50ff"}
ROUTINE_ALERT = {"text": "routine bot-board-issues failed: the check exited 1: error connecting to api.github.com",
                 "href": "/routines#r=2", "where": "routines", "key": "2a94fa81e4402a9f"}


def bot(handle, status="stopped", profile=None, last_run="", last_post="", health="", deleted=None):
    return {"handle": handle, "status": status, "profile": profile, "last_run": last_run, "last_post": last_post,
            "health": health, "deleted": deleted, "want": "stopped" if status == "stopped" else "running"}


class Board:
    """Serves canned JSON for the two paths the check reads."""
    def __init__(self, status=None, fleet=None, fail=False):
        self.status, self.fleet, self.fail = status or {"attention": []}, fleet or {"hosts": []}, fail

    def __enter__(self):
        board = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                if board.fail:
                    self.send_response(503); self.end_headers(); return
                data = {"/api/status": board.status, "/api/fleet": board.fleet}.get(self.path)
                body = json.dumps(data if data is not None else {"detail": "not found"}).encode()
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        return self

    def __exit__(self, *exc):
        self.server.shutdown(); self.server.server_close()


def run(board, now="2026-10-30T07:30:00+00:00", watermark=None):
    env = {**os.environ, "BOARD": board.url, "ROUTINE_NOW": now,
           "ROUTINE_WATERMARK": "" if watermark is None else json.dumps(watermark)}
    r = subprocess.run([sys.executable, str(CHECK)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), r.stderr


class Check(unittest.TestCase):
    def test_a_quiet_board_wakes_nobody(self):
        with Board() as b:
            out, _ = run(b)
        self.assertEqual(out, {"work": False})

    def test_before_seven_utc_and_twice_a_day_it_stays_quiet(self):
        with Board({"attention": [ALERT]}) as b:
            self.assertEqual(run(b, now="2026-10-30T06:59:00+00:00")[0], {"work": False})
            self.assertEqual(run(b, watermark={"day": "2026-10-30", "seen": {}})[0], {"work": False})

    def test_alerts_and_dead_bots_make_the_agenda(self):
        fleet = {"hosts": [{"name": "minipc-1", "bots": [
            bot("leo", last_run="2026-10-08T23:48:57+00:00"),                                  # dead: 22 days
            bot("iris", last_run="2026-10-16T15:15:30+00:00", health="out of codex usage since 2026-10-16"),
            bot("sven", profile="development-manager", last_run="2026-10-28T22:15:00+00:00"),   # kept on purpose
            bot("lola", status="waiting", last_run="2026-10-30T06:00:00+00:00"),              # running
            bot("pia"),                                                                      # never ran
            bot("gone", deleted="2026-10-30T06:00:00+00:00"),                                # already deleted
            bot("fresh", last_run="2026-10-27T00:00:00+00:00"),                              # 3 days: not yet
        ]}]}
        with Board({"attention": [ALERT, ROUTINE_ALERT]}, fleet) as b:
            out, _ = run(b)
        self.assertTrue(out["work"])
        text = "\n".join(out["found"])
        self.assertIn("this is the first care job", out["found"][0])
        self.assertIn("New since the last job (5)", text)
        self.assertIn("fleet: @cleo on jacs-m5: logged out", text)
        self.assertIn("routines: routine bot-board-issues failed", text)
        self.assertIn("@leo on minipc-1: stopped, no profile, last ran 2026-10-08", text)
        self.assertIn("@iris on minipc-1", text); self.assertIn("health line still says: out of codex usage", text)
        self.assertIn("@pia on minipc-1: stopped, no profile, last ran never", text)
        for kept in ("@sven", "@lola", "@gone", "@fresh"):
            self.assertNotIn(kept, text)
        self.assertIn(b.url + "/fleet#bot-cleo", text, "links are absolute, for a bot on another box")
        mark = out["watermark"]
        self.assertEqual((mark["day"], mark["full"]), ("2026-10-30", "2026-10-30"))
        self.assertEqual(set(mark["seen"]), {"alert:5abd0f14ac8d50ff", "alert:2a94fa81e4402a9f",
                                             "bot:leo@minipc-1", "bot:iris@minipc-1", "bot:pia@minipc-1"})
        self.assertTrue(all(v == "2026-10-30" for v in mark["seen"].values()))

    def test_the_next_day_only_new_items_wake_the_bot_and_a_week_later_everything_does(self):
        fleet = {"hosts": [{"name": "minipc-1", "bots": [bot("leo", last_run="2026-10-08T23:48:57+00:00")]}]}
        seen = {"alert:5abd0f14ac8d50ff": "2026-10-30", "bot:leo@minipc-1": "2026-10-30"}
        mark = {"day": "2026-10-30", "seen": seen, "full": "2026-10-30"}
        with Board({"attention": [ALERT]}, fleet) as b:
            # the same two items the next day: nothing new, no job
            self.assertEqual(run(b, now="2026-10-31T08:00:00+00:00", watermark=mark)[0], {"work": False})
            # one new alert: a job with the new item only, the standing ones kept in the watermark
            b.status = {"attention": [ALERT, ROUTINE_ALERT]}
            out, _ = run(b, now="2026-10-31T08:00:00+00:00", watermark=mark)
            self.assertTrue(out["work"])
            text = "\n".join(out["found"])
            self.assertIn("New since the last job (1)", text)
            self.assertIn("bot-board-issues", text)
            self.assertNotIn("Still standing", text, "not a full agenda: the standing items are not repeated")
            self.assertIn("Full agenda: no", text)
            self.assertEqual(out["watermark"]["seen"]["bot:leo@minipc-1"], "2026-10-30", "first seen is kept")
            self.assertEqual(out["watermark"]["seen"]["alert:2a94fa81e4402a9f"], "2026-10-31")
            self.assertEqual(out["watermark"]["full"], "2026-10-30")
            # a week after the last full agenda, with nothing new: everything, with the day first seen
            b.status = {"attention": [ALERT]}
            out, _ = run(b, now="2026-11-06T08:00:00+00:00", watermark=mark)
            self.assertTrue(out["work"])
            text = "\n".join(out["found"])
            self.assertIn("Full agenda: yes", text)
            self.assertIn("Still standing (2)", text)
            self.assertIn("[since 2026-10-30]", text)
            self.assertEqual(out["watermark"]["full"], "2026-11-06")

    def test_an_item_that_cleared_is_dropped_and_counts_as_new_if_it_returns(self):
        mark = {"day": "2026-10-30", "seen": {"alert:5abd0f14ac8d50ff": "2026-10-30"}, "full": "2026-10-30"}
        with Board({"attention": [ROUTINE_ALERT]}) as b:
            out, _ = run(b, now="2026-10-31T08:00:00+00:00", watermark=mark)
        self.assertTrue(out["work"])
        self.assertEqual(set(out["watermark"]["seen"]), {"alert:2a94fa81e4402a9f"}, "the cleared alert left the watermark")

    def test_a_board_that_does_not_answer_is_not_a_failure(self):
        with Board(fail=True) as b:
            out, err = run(b)
        self.assertEqual(out, {"work": False})
        self.assertIn("did not answer", err)


if __name__ == "__main__":
    unittest.main()
