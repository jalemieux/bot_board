"""The Routines tab of the workspace page (#820, option B): one card per routine of the workspace, and in its folded Details
what the old /routines page held. Read only for anyone who cannot change a routine, said in a box, not in greyed buttons.
Everything from the API goes on the page as text. Joined into WS_JS and WS_CSS by app/workspace_web.py."""

ROUTINES_CSS = """
.ws .rtbox{padding:10px 12px;margin:8px 0 12px;border-left:4px solid var(--line);background:var(--panel);font-size:13px}
.ws .rtcard{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin:0 0 12px}
.ws .rtcard.bad{border-left:4px solid var(--accent)}
.ws .rtcard h3{margin:0 0 4px;font-size:15px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}
.ws .rtcard .st{font:600 11px var(--mono);border-radius:4px;padding:1px 7px;background:var(--line);color:var(--ink)}
.ws section.routines{border:0;background:none;padding:0;margin:0}
.ws .rtcard .st.enabled{background:var(--good);color:#fff}.ws .rtcard .st.waits{background:none;color:var(--accent);box-shadow:inset 0 0 0 2px var(--accent)}.ws .rtcard .st.failed{background:var(--accent);color:#fff}
.ws .rtcard .about{margin:0 0 8px;font-size:14px}
.ws .rtcard .prob{color:var(--accent);font-size:13px;white-space:pre-wrap;margin:6px 0}
.ws .rtcard .facts{color:var(--dim);font:12px var(--mono);margin:6px 0 8px}
.ws .rtcard .acts{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.ws .rtcard .acts button,.ws .rtcard .acts a.btn{min-height:44px;padding:6px 14px;font:600 12px var(--mono);border-radius:6px;border:0;background:var(--accent-soft);color:var(--accent);cursor:pointer;display:inline-flex;align-items:center;text-decoration:none}
.ws .rtcard .acts button.primary{background:var(--accent);color:#fff}
.ws .rtcard pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0 10px;font:12px/1.5 var(--mono)}
.ws .rtcard .add{color:var(--good)}.ws .rtcard .del{color:var(--accent)}
.ws .rtcard details summary{cursor:pointer;color:var(--dim);font-size:13px;min-height:32px}
.ws .rtcard dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:13px;margin:6px 0 10px}
.ws .rtcard dt{color:var(--dim);font:12px var(--mono)}.ws .rtcard dd{margin:0}
.ws .rtcard table{width:100%;border-collapse:collapse;font-size:13px}
.ws .rtcard td,.ws .rtcard th{padding:6px 8px 6px 0;border-top:1px solid var(--line);text-align:left;vertical-align:top}
@media (max-width:700px){.ws .rtcard .acts button,.ws .rtcard .acts a.btn{flex:1 1 100%;justify-content:center}}
"""

ROUTINES_JS = r"""
// The Routines tab (#820, option B). `canAct` is whether this person manages the admin workspace; the API is the judge.
function fmtDur(n) {
  n = +n; if (!isFinite(n) || n < 0) return '';
  if (n === 0) return '0s';
  var out = '';
  [['d', 86400], ['h', 3600], ['m', 60], ['s', 1]].forEach(function (x) { var q = Math.floor(n / x[1]); if (q) { out += q + x[0]; n -= q * x[1]; } });
  return out;
}
function when(ts) { var d = new Date(ts); return isNaN(d) ? (ts || '') : d.toLocaleString(); }
function diffLines(text) {
  return String(text || '').split('\n').map(function (l) {
    return h('div', {'class': l.charAt(0) === '+' ? 'add' : l.charAt(0) === '-' ? 'del' : null, text: l || ' '});
  });
}
function routinesView(w) {
  var sec = h('section', {'class': 'routines'}), body = h('div', {'aria-live': 'polite'});
  sec.appendChild(h('p', {'class': 'sub', text: 'What runs on a timer in ' + (w.name || w.slug) + ', and how it is doing.'}));
  sec.appendChild(body);
  root.appendChild(sec);
  var openId = (/^#routines\/(\d+)$/.exec(location.hash) || [])[1];
  var load = function () {
    body.textContent = '';
    body.appendChild(h('p', {'class': 'sub', 'aria-busy': 'true', text: 'Loading routines…'}));
    Promise.all([api('GET', '/api/routines?workspace=' + encodeURIComponent(w.slug)),
                 api('GET', '/api/workspaces/admin').then(function (d) { return !!(d.workspace && d.workspace.can_manage); }, function () { return false; })])
      .then(function (r) { draw(r[0].routines || [], ME && r[1]); })
      .catch(function (e) {
        body.textContent = '';
        var retry = h('button', {type: 'button', 'class': 'quiet', text: 'Retry'}); retry.addEventListener('click', load);
        body.appendChild(h('p', {'class': 'msg-err', role: 'alert'}, e.message + ' ', retry));
      });
  };
  var draw = function (rs, canAct) {
    body.textContent = '';
    if (!canAct) body.appendChild(h('div', {'class': 'rtbox', role: 'status'},
      'You can read these. Only the admin workspace’s owner and the admin bots change, pause or approve a routine. To ask for a change, post in the admin workspace’s channel; each routine has a link to its own thread.'));
    if (!rs.length) {
      body.appendChild(h('div', {'class': 'empty'}, h('h3', {text: 'No routines in ' + (w.name || w.slug) + '.'}),
        h('p', {text: 'A routine is a check that runs on a timer and posts here only when it finds work. To get one, ask in the admin workspace’s channel: the admin bot works it out with you and the owner approves it.'}),
        h('p', null, h('a', {href: '/c/admin', text: 'Ask in #admin'}))));
      return;
    }
    rs.forEach(function (r) { body.appendChild(card(r, canAct)); });
  };
  var card = function (r, canAct) {
    var name = r.name || ('#' + r.id), out = h('div', {role: 'status', 'aria-live': 'polite'}), path = '/api/routines/' + encodeURIComponent(r.id);
    var waiting = r.waiting && !r.problem, bad = r.state === 'failed' || !!r.problem;
    var jobs = r.jobs || [], last = jobs[0];
    var call = function (btn, method, p, ask, body_) {
      if (ask && !window.confirm(ask)) return;
      btn.disabled = true;
      api(method, p, body_).then(function () { load(); }, function (e) { btn.disabled = false; note(out, e.message); });
    };
    var button = function (text, cls, fn) { var b = h('button', {type: 'button', 'class': cls || null, text: text}); b.addEventListener('click', function () { fn(b); }); return b; };
    var acts = h('div', {'class': 'acts'});
    if (canAct) {
      if (waiting) acts.appendChild(button('Approve', 'primary', function (b) { call(b, 'POST', path + '/approve'); }));
      if (r.upgrade) acts.appendChild(button('Approve the upgrade', 'primary', function (b) {
        var shown = {}; shown[r.id] = r.version;
        call(b, 'POST', '/api/routines/upgrades/approve', null, {recipe_version: r.upgrade.to, routines: shown});
      }));
      if (r.state === 'enabled') {
        acts.appendChild(button('Run now', null, function (b) { call(b, 'POST', path + '/run'); }));
        acts.appendChild(button('Pause', null, function (b) { call(b, 'POST', path + '/pause'); }));
      } else if (!r.problem) acts.appendChild(button(r.state === 'failed' ? 'Enable again' : 'Enable', 'primary', function (b) { call(b, 'POST', path + '/enable'); }));
    } else {
      var see = h('a', {href: '#routines/' + r.id, 'class': 'btn', text: 'See the routine'});
      see.addEventListener('click', function () { var d = document.getElementById('routine-' + r.id); if (d) d.open = true; });
      acts.appendChild(see);
      acts.appendChild(h('a', {href: r.thread ? '/t/' + encodeURIComponent(r.thread) : '/c/admin', 'class': 'btn', text: 'Ask for a change in #admin'}));
    }
    var details = h('details', {id: 'routine-' + r.id}, h('summary', {text: 'Details'}),
      h('dl', null, r.thread ? [h('dt', {text: 'asked in'}), h('dd', null, h('a', {href: '/t/' + encodeURIComponent(r.thread), text: '#' + r.thread}))] : null,
        h('dt', {text: 'version'}), h('dd', {text: String(r.version) + (r.updated_at ? ', ' + when(r.updated_at) : '')}),
        h('dt', {text: 'claim within'}), h('dd', {text: fmtDur(r.claim_timeout)}), h('dt', {text: 'done within'}), h('dd', {text: fmtDur(r.done_timeout)}),
        h('dt', {text: 'sent again'}), h('dd', {text: (r.resends != null ? r.resends : '?') + ' time(s) before it counts as failed'}),
        r.skill ? [h('dt', {text: 'needs skill'}), h('dd', {text: r.skill})] : null),
      r.last_test ? [h('h4', {text: 'Last test'}), h('p', {'class': r.last_test.ok ? 'msg-ok' : 'msg-err', text: (r.last_test.ok ? (r.last_test.work ? 'Found work.' : 'Ran fine, nothing to do.') : 'The check failed.') + (r.last_test.at ? ' ' + when(r.last_test.at) : '')}),
        r.last_test.error ? h('pre', {'class': 'prob', text: r.last_test.error}) : null] : null,
      r.last_run ? [h('h4', {text: 'Last run now'}), h('p', {text: (r.last_run.result || '?') + (r.last_run.at ? ', ' + when(r.last_run.at) : '')})] : null,
      h('h4', {text: 'Latest jobs'}),
      jobs.length ? h('table', null, jobs.map(function (j) {
        return h('tr', null, h('td', null, h('a', {href: '/t/' + encodeURIComponent(j.id), text: '#' + j.id}), j.channel ? ' in #' + j.channel : ''),
          h('td', {text: when(j.created_at)}), h('td', {text: j.done_at ? 'by @' + (j.done_by || '?') + ', ' + when(j.done_at) : 'open'}));
      })) : h('p', {'class': 'sub', text: 'Nothing fired yet.'}),
      r.recipe ? [h('h4', {text: 'What it does'}), h('dl', null,
        h('dt', {text: 'watches'}), h('dd', {text: r.recipe.repo + (r.recipe.forgejo ? ' on Forgejo' : '') + ', ' + r.recipe.batch + ' at a time'}),
        h('dt', {text: 'posts to'}), h('dd', {text: r.recipe.workspace || r.recipe.project || ''})),
        (r.items || []).length ? h('table', null, r.items.map(function (i) {
          return h('tr', null, h('td', {text: '#' + i.number}), h('td', null, h('a', {href: '/t/' + encodeURIComponent(i.id), text: 'thread ' + i.id})),
            h('td', {text: i.worker ? i.worker : 'nobody'}));
        })) : h('p', {'class': 'sub', text: 'Nothing announced yet.'})] : null,
      h('h4', {text: 'Prompt'}), h('pre', {text: r.prompt || ''}),
      h('h4', {text: 'Check'}), h('pre', {text: r.check || ''}),
      h('p', {'class': 'sub', text: 'The check runs on the runner’s box with its credentials.'}),
      canAct ? button('Delete this routine', 'quiet', function (b) { call(b, 'DELETE', path, 'Delete the routine ' + name + '? It stops for good.'); }) : null);
    if (String(openId) === String(r.id)) details.open = true;
    return h('article', {'class': 'rtcard' + (bad || waiting ? ' bad' : ''), id: 'r-' + r.id},
      h('h3', null, name, h('span', {'class': 'st ' + (waiting ? 'waits' : (r.state || '')), text: waiting && r.state === 'enabled' ? 'enabled, a new version waits' : (r.state || '?')})),
      r.about ? h('p', {'class': 'about', text: r.about}) : null,
      r.problem ? h('p', {'class': 'prob', text: 'Invalid, so it never runs: ' + r.problem}) : null,
      r.failure ? h('p', {'class': 'prob'}, 'Failed: ' + r.failure + (r.state === 'failed' ? ' It has stopped.' : ''), r.notify ? ' The admin workspace’s owner (@' + r.notify + ') has been told.' : '') : null,
      waiting ? h('div', null, h('p', {'class': 'prob', text: 'Version ' + r.version + ' is waiting for a yes' + (r.state === 'enabled' ? '. The version before it keeps running until it is approved.' : '.') + (r.waiting.changed ? ' What changed: ' + r.waiting.changed + '.' : '')}),
        r.waiting.diff ? h('pre', null, diffLines(r.waiting.diff)) : null) : null,
      r.upgrade ? h('p', {'class': 'prob', text: 'An upgrade to recipe version ' + r.upgrade.to + ' waits for a yes: this release writes the check and prompt again, settings kept.'}) : null,
      h('div', {'class': 'facts', text: 'fires in #' + r.channel + ' · every ' + fmtDur(r.every) + ' · ' + (last ? 'last job #' + last.id + (last.done_at ? ', done' : ', open') : 'nothing fired yet')}),
      acts, out, details);
  };
  load();
}
"""
