"""Exercise the launcher's actual shell functions with a fake harness."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'bin/bot').read_text()


class UsageLimits(unittest.TestCase):
    def run_shell(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            run = SOURCE[SOURCE.index('run_harness() {'):SOURCE.index('# ----', SOURCE.index('run_harness() {'))]
            detection = SOURCE[SOURCE.index('LIMIT_RE='):SOURCE.index('said_logged_out()')]
            script = '''set -euo pipefail
STATE=$TEST_DIR
LOG=$STATE/log
: > "$LOG"
HARNESS=claude
BOT_CLAUDE_FLAGS=""
load_fleet_env() { :; }
''' + detection + run + body
            r = subprocess.run(['bash', '-c', script], env={**os.environ, 'TEST_DIR': tmp}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_weekly_limit_is_kept_until_success_including_zero_exit(self):
        self.run_shell('''
claude() { echo "You've hit your weekly limit · resets Oct 19 at 6am (America/Los_Angeles)"; return 0; }
if run_harness "$STATE" "$STATE/session" prompt; then exit 10; fi
grep -q 'out of claude usage' "$STATE/health"
grep -q 'resets Oct 19 at 6am' "$STATE/health"
[ ! -s "$STATE/session" ]
claude() { echo 'ordinary failure'; return 1; }
if run_harness "$STATE" "$STATE/session" prompt; then exit 11; fi
grep -q 'weekly limit' "$STATE/health"
claude() { echo 'work completed'; }
run_harness "$STATE" "$STATE/session" prompt
[ ! -e "$STATE/health" ]
[ -s "$STATE/session" ]
''')

    def test_stopped_bot_reports_persistent_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / '.config/bot_board/bots/opal'
            state.mkdir(parents=True)
            (state / 'harness').write_text('claude')
            (state / 'health').write_text('out of claude usage: weekly limit; resets Monday')
            r = subprocess.run(['bash', str(ROOT / 'bin/bot'), 'ps', '-a', '--tsv'],
                               env={**os.environ, 'HOME': tmp, 'BOT_CONFIG': tmp + '/missing'},
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('stopped', r.stdout)
            self.assertIn('weekly limit; resets Monday', r.stdout)

    def test_no_match_does_not_exit_and_old_log_does_not_match(self):
        self.run_shell('''
echo 'usage limit reached' >> "$LOG"
off=$(wc -c < "$LOG")
echo 'ordinary failure' >> "$LOG"
limit=$(said_limited "$off")
[ -z "$limit" ]
for message in 'You have exceeded your quota' 'usage_limit_reached' 'rate limit reached'; do
  off=$(wc -c < "$LOG")
  echo "$message" >> "$LOG"
  [ -n "$(said_limited "$off")" ]
done
''')
