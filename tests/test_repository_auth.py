import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('auth_test', ROOT / 'bin/repository_auth.py')
auth = importlib.util.module_from_spec(spec); spec.loader.exec_module(auth)


class RepositoryAuth(unittest.TestCase):
    def test_only_managed_repository_tokens_override_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fleet.env'
            base = {'GH_TOKEN':'personal', 'GITHUB_TOKEN':'other-personal', 'OTHER_KEY':'keep'}
            self.assertEqual(auth.token_environment(base, path), {'OTHER_KEY':'keep'})
            path.write_text('GH_TOKEN=hand-written\n')
            self.assertEqual(auth.token_environment(base, path), {'OTHER_KEY':'keep'})
            path.write_text(auth.HEADER + '\nGH_TOKEN=board\nGH_ENTERPRISE_TOKEN=enterprise\n')
            env = auth.token_environment(base, path)
            self.assertEqual(auth.token_for('github.com', env), 'board')
            self.assertEqual(auth.token_for('my.ghe.com', env), 'board')
            self.assertEqual(auth.token_for('git.example.com', env), 'enterprise')
            path.write_text(auth.HEADER + '\nGH_TOKEN=rotated\n')
            self.assertEqual(auth.token_environment(base, path)['GH_TOKEN'], 'rotated')
            path.write_text(auth.HEADER + '\n')
            self.assertNotIn('GH_TOKEN', auth.token_environment(base, path))

    def test_real_git_credential_flow_never_falls_back_to_saved_helper(self):
        with tempfile.TemporaryDirectory() as home:
            cfg = Path(home) / '.config/bot_board'; cfg.mkdir(parents=True)
            helper = '!' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'bin/repository_auth.py'))
            env = {**os.environ, 'HOME':home, 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_TERMINAL_PROMPT':'0',
                   'GIT_CONFIG_GLOBAL':'/dev/null', 'GH_HOST':'git.example.com',
                   'GIT_CONFIG_COUNT':'3', 'GIT_CONFIG_KEY_0':'credential.helper',
                   'GIT_CONFIG_VALUE_0':'!echo username=personal; echo password=personal',
                   'GIT_CONFIG_KEY_1':'credential.helper', 'GIT_CONFIG_VALUE_1':'',
                   'GIT_CONFIG_KEY_2':'credential.helper', 'GIT_CONFIG_VALUE_2':helper,
                   'GH_TOKEN':'personal-inherited'}
            def fill(host='github.com'):
                return subprocess.run(['git','credential','fill'], env=env,
                    input='protocol=https\nhost=' + host + '\n\n', capture_output=True, text=True)
            r = fill()
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn('personal', r.stdout + r.stderr)
            self.assertIn('fleet Secrets', r.stderr)
            path = cfg / 'fleet.env'
            path.write_text(auth.HEADER + '\nGH_TOKEN=board-test-token\nGH_ENTERPRISE_TOKEN=enterprise-test-token\n')
            self.assertIn('password=board-test-token', fill().stdout)
            self.assertIn('password=enterprise-test-token', fill('git.example.com').stdout)
            self.assertNotEqual(fill('unconfigured.example.com').returncode, 0)
            path.write_text(auth.HEADER + '\nGH_TOKEN=rotated-test-token\n')
            self.assertIn('password=rotated-test-token', fill().stdout)
            path.write_text(auth.HEADER + '\n')
            self.assertNotEqual(fill().returncode, 0)


if __name__ == '__main__': unittest.main()
