"""A launcher's failure notices cannot start a loop between bots.

On 2026-10-15 two bots whose harness kept failing answered each other's
"my harness exited" notice, then a usage limit pulled two more in: about 450
posts. These run the real bin/bot against the fake board of
test_project_routing, with a fake claude that fails on purpose.
"""
import json, os, unittest
import test_project_routing as routing
from test_project_routing import FakeBoard, msg

FAILING_CLAUDE = """#!/usr/bin/env python3
import sys, os, json
a = sys.argv[1:]
with open(os.environ["ROUTING_TEST_RUNS"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "prompt": a[-1]}) + "\\n")
if "read the rules and return" in a[-1]:
    sys.exit(0)
print(os.environ.get("FAIL_WITH", "something broke"))
sys.exit(1)
"""


class Loop(routing.Routing):
    def fail_with(self, text):
        with open(os.path.join(self.tmp, "bin", "claude"), "w") as f:
            f.write(FAILING_CLAUDE)
        self.env["FAIL_WITH"] = text

    def state(self, name):
        p = os.path.join(self.home, ".config/bot_board/bots/tst", name)
        return open(p).read() if os.path.exists(p) else ""

    def test_a_launcher_notice_never_reaches_the_harness(self):
        board = FakeBoard([
            msg(1, "help", "@tst my harness (claude) exited with code 1 before replying.", mention=True,
                meta={"launcher": "bin/bot"}),
            msg(2, "help", "a real question"),
        ], {})
        try:
            runs = self.run_bot(board, lambda rs: any("a real question" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        self.assertIn("a real question", prompts)
        self.assertNotIn("exited with code 1", prompts)

    def test_a_usage_limit_holds_the_batch_and_says_so_once(self):
        self.fail_with("You've hit your session limit · resets 12:50am (UTC)")
        board = FakeBoard([msg(1, "help", "@tst please look", mention=True), msg(2, "help", "and this")], {})
        try:
            runs = self.run_bot(board, lambda rs: False, timeout=8)
        finally:
            board.close()
        self.assertEqual(len([r for r in runs if "New message" in r["prompt"]]), 1, "retried while limited")
        self.assertEqual(len(board.posts), 1, board.posts)
        self.assertIn("hit a limit", board.posts[0]["body"])
        self.assertNotIn("@", board.posts[0]["body"], "a mention would wake another bot")
        self.assertTrue(self.state("health").startswith("out of claude usage"))
        self.assertEqual(sorted(l.split("\t")[0] for l in self.state("held").splitlines()), ["1", "2"])

    def test_repeated_failures_post_one_notice(self):
        self.fail_with("Error: something broke")
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "help", "@tst one", mention=True), msg(2, "wordsnap", "@tst two", mention=True)],
                          {}, has_lookup=False)
        try:
            runs = self.run_bot(board, lambda rs: False, timeout=6)
        finally:
            board.close()
        self.assertGreaterEqual(len([r for r in runs if "New message" in r["prompt"]]), 2)
        self.assertEqual(len(board.posts), 1, [p["body"] for p in board.posts])
        self.assertTrue(self.state("health").startswith("last run failed"))


# Loop borrows Routing's fixtures, not its tests
for name in [n for n in dir(routing.Routing) if n.startswith("test_")]:
    setattr(Loop, name, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
