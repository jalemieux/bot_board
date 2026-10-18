"""Fleet-only repository credentials; never consult a user's saved gh accounts."""
import os
from pathlib import Path
import re
import shlex
import sys

TOKEN_NAMES = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')
HEADER = '# written by bin/keeper from the board'


def fleet_tokens(path):
    try:
        text = Path(path).read_text()
    except OSError:
        return {}
    if not text.startswith(HEADER):
        return {}
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if sep and key in TOKEN_NAMES:
            try:
                values = shlex.split(value, comments=True)
            except ValueError:
                continue
            if values and '\n' not in values[0] and '\0' not in values[0]:
                result[key] = values[0]
    return result


def token_environment(base, path):
    env = {k:v for k,v in base.items() if k not in TOKEN_NAMES}
    env.update(fleet_tokens(path))
    return env


def token_name(host):
    return 'GH_TOKEN' if host == 'github.com' or host.endswith('.ghe.com') else 'GH_ENTERPRISE_TOKEN'


def token_for(host, env):
    key = token_name(host)
    alias = 'GITHUB_TOKEN' if key == 'GH_TOKEN' else 'GITHUB_ENTERPRISE_TOKEN'
    return env.get(key) or env.get(alias)


def credential(action):
    if action != 'get':
        return
    fields = dict(line.rstrip('\n').split('=', 1) for line in sys.stdin if '=' in line)
    host = fields.get('host', '').lower()
    if fields.get('protocol') != 'https' or not re.fullmatch(r'[a-z0-9.-]+', host):
        print('quit=true\n')
        return
    env = token_environment(os.environ, Path.home() / '.config/bot_board/fleet.env')
    # Enterprise Git remotes must opt into the configured host, not arbitrary URLs.
    allowed = host == 'github.com' or host.endswith('.ghe.com') or host == env.get('GH_HOST', '').lower()
    token = token_for(host, env) if allowed else None
    if not token:
        print(f'repository access: configure {token_name(host)} in fleet Secrets for {host}', file=sys.stderr)
        print('quit=true\n')
        return
    print('username=x-access-token\npassword=' + token + '\n')


if __name__ == '__main__':
    credential(sys.argv[1] if len(sys.argv) > 1 else '')
