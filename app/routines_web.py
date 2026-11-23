"""The old /routines page, gone (#820): each workspace has a Routines tab (app/workspace_routines_web.py). What is left is the address,
so an old link or bookmark lands where its routine is shown. The script does it, because the server never sees a link's #fragment:
#r=<id> asks the API which workspace the routine is in; anything else goes to the workspaces, which say where the routines went."""
from __future__ import annotations

from .web import shell

MOVED_JS = r"""
(function () {
  var back = function () { location.replace('/workspaces?from=routines'); };
  if (/^#approvals/.test(location.hash)) { location.replace('/workspaces/admin#routines'); return; }
  var m = /^#r=(\d+)/.exec(location.hash);
  if (!m) { back(); return; }
  fetch('/api/routines/' + m[1]).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (d && d.workspace) location.replace('/workspaces/' + encodeURIComponent(d.workspace) + '#routines/' + m[1]); else back();
  }).catch(back);
})();
"""


def routines_moved_page(board_name: str) -> str:
    return shell(board_name, "workspaces", here="/workspaces", content=
                 '<p class="sub">Routines moved: each workspace has a Routines tab. <a href="/workspaces?from=routines">Open the workspaces</a>.</p>'
                 '<script>' + MOVED_JS + '</script>')
