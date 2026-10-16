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
            detection = SOURCE[SOURCE.index('LIMIT_RE='):SOURCE.index('# notice <board>')]
            script = '''set -euo pipefail
STATE=$TEST_DIR
LOG=$STATE/log
: > "$LOG"
HARNESS=claude
BOT_CLAUDE_FLAGS=""
BOT_CODEX_FLAGS=""
load_fleet_env() { :; }
''' + detection + run + body
            r = subprocess.run(['bash', '-c', script], env={**os.environ, 'TEST_DIR': tmp}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_codex_resumed_runs_ignore_quoted_limits_in_messages_and_tool_output(self):
        self.run_shell('''
HARNESS=codex
echo existing-session > "$STATE/session"
codex() {
  printf '%s\\n' "$@" > "$STATE/args"
  cat <<'JSON'
{"type":"item.completed","item":{"type":"agent_message","text":"Confirmed: matches session limit|usage limit|rate limit in the output"}}
{"type":"item.completed","item":{"type":"command_execution","aggregated_output":"ERROR: usage limit reached"}}
{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}
JSON
}
echo 'out of codex usage: false previous alarm' > "$STATE/health"
run_harness "$STATE" "$STATE/session" 'quoted usage limit reached'
grep -qx -- --json "$STATE/args"
[ ! -e "$STATE/health" ]
''')

    def test_codex_defaults_preserve_sandbox_and_allow_board_access(self):
        default = next(line for line in SOURCE.splitlines() if line.startswith('BOT_CODEX_FLAGS='))
        self.run_shell('''
HARNESS=codex
unset BOT_CODEX_FLAGS
''' + default + '''
codex() {
  printf '%s\\n' "$@" > "$STATE/args"
  echo '{"type":"thread.started","thread_id":"first-session"}'
}
run_harness "$STATE" "$STATE/session" prompt
grep -qx workspace-write "$STATE/args"
grep -qx sandbox_workspace_write.network_access=true "$STATE/args"
grep -qx -- --json "$STATE/args"
BOT_CODEX_FLAGS='-s read-only'
run_harness "$STATE" "$STATE/session" prompt
grep -qx read-only "$STATE/args"
! grep -q network_access "$STATE/args"
''')

    def test_codex_provider_errors_hold_messages_including_zero_exit(self):
        self.run_shell('''
HARNESS=codex
echo existing-session > "$STATE/session"
codex() {
  echo '{"type":"turn.failed","error":{"message":"You have exceeded your quota","code":"insufficient_quota"}}'
}
if run_harness "$STATE" "$STATE/session" prompt; then exit 20; fi
grep -q 'out of codex usage' "$STATE/health"
grep -q 'exceeded your quota' "$STATE/health"
codex() { echo '{"type":"error","message":"usage_limit_reached"}'; return 1; }
if run_harness "$STATE" "$STATE/session" prompt; then exit 21; fi
grep -q usage_limit_reached "$STATE/health"
codex() { echo 'ERROR: 429 Too Many Requests' >&2; return 1; }
if run_harness "$STATE" "$STATE/session" prompt; then exit 22; fi
grep -q 'Too Many Requests' "$STATE/health"
''')

    def test_codex_first_run_records_session_and_ignores_nonerror_content(self):
        self.run_shell('''
HARNESS=codex
codex() {
  cat <<'JSON'
{"type":"thread.started","thread_id":"new-session"}
{"type":"item.completed","item":{"type":"agent_message","text":"We fixed usage_limit_reached detection."}}
{"type":"turn.completed"}
JSON
}
run_harness "$STATE" "$STATE/session" prompt
[ "$(cat "$STATE/session")" = new-session ]
[ ! -e "$STATE/health" ]
''')

    def test_codex_ordinary_failure_is_not_a_quota_error(self):
        self.run_shell('''
HARNESS=codex
echo existing-session > "$STATE/session"
codex() {
  echo '{"type":"item.completed","item":{"type":"agent_message","text":"usage limit reached"}}'
  echo '{"type":"turn.failed","error":{"message":"sandbox failed"}}'
  return 1
}
if run_harness "$STATE" "$STATE/session" prompt; then exit 30; fi
[ -z "$(said_limited 0)" ]
[ ! -e "$STATE/health" ]
echo '[]' >> "$LOG"
echo 'not-json' >> "$LOG"
[ -z "$(said_limited 0)" ]
''')

    def test_codex_login_detection_ignores_agent_text_and_reads_errors(self):
        self.run_shell('''
HARNESS=codex
echo '{"type":"item.completed","item":{"type":"agent_message","text":"Do not run codex login; already logged in."}}' >> "$LOG"
if said_logged_out 0; then exit 40; fi
echo '{"type":"error","message":"Not logged in. Run codex login."}' >> "$LOG"
said_logged_out 0
''')

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
