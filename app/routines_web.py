"""The routines page: recurring work, created and switched from the board.

A routine stays a message in the `admin` channel (bin/routines); this page is a
window onto those threads. It lists them from GET /api/routines, creates one or
a new version through POST /api/routines, and enable, pause and test are the
same replies a human would type in the routine's thread, posted for them. Writes
use the identity the board page keeps in this browser (localStorage bb.me);
whether an `enable` counts is still the runner's call (ROUTINES_OPERATORS). Data
from the API is only ever put on the page as text, never as HTML.
"""
from __future__ import annotations

from .web import shell

ROUTINES_CSS = """
.rt .bar{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;margin:8px 0 14px;font-size:13px;color:var(--dim)}
.rt .badge{display:inline-block;border-radius:4px;padding:0 6px;font:11px var(--mono);background:var(--line);color:var(--dim);margin-left:6px;vertical-align:middle}
.rt .badge.enabled{background:var(--good);color:#fff}.rt .badge.paused{background:var(--line);color:var(--ink)}
.rt .badge.failed{background:var(--accent);color:#fff}.rt .badge.invalid{background:var(--warn);color:#000}
.rt section{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin:0 0 14px}
.rt section h3{margin:0 0 8px;font-size:15px}
.rt form{display:grid;gap:8px;margin:6px 0 0}
.rt label{display:grid;gap:3px;font-size:12px;color:var(--dim);font-family:var(--mono)}
.rt input[type=text],.rt input[type=number],.rt textarea{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:6px 8px;font:13px/1.45 var(--mono);width:100%}
.rt input.short{width:9em}
.rt textarea{min-height:8em;resize:vertical}
.rt textarea.code{min-height:14em;white-space:pre;tab-size:4}
.rt button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 13px;font:600 12px var(--mono);cursor:pointer;justify-self:start}
.rt button.quiet{background:var(--accent-soft);color:var(--accent)}
.rt button:disabled{opacity:.5;cursor:default}
.rt .row{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:flex-end}
.rt .note{font-size:12px;color:var(--dim)}
.rt .msg-ok{color:var(--good);font-size:13px}.rt .msg-err{color:var(--accent);font-size:13px;white-space:pre-wrap}
.rt .prob{color:var(--accent);font-size:13px;white-space:pre-wrap;margin:6px 0}
.rt pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0 10px}
.rt table{width:100%;border-collapse:collapse;font-size:13px}
.rt td,.rt th{padding:6px 8px 6px 0;border-top:1px solid var(--line);text-align:left;vertical-align:top}
.rt th{font:600 12px var(--mono);color:var(--dim)}
.rt td.mono{font-family:var(--mono);font-size:12px}
.rt dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:13px;margin:6px 0 10px}
.rt dt{color:var(--dim);font-family:var(--mono);font-size:12px}.rt dd{margin:0}
.rt details summary{cursor:pointer;color:var(--dim);font-size:13px}
.rt h4{margin:10px 0 0;font:600 11.5px var(--mono);text-transform:uppercase;letter-spacing:.09em;color:var(--dim)}
.rt .srcs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.rt .src{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font:500 13px inherit}
.rt .src[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent);font-weight:600}
.rt .src:disabled{opacity:.4}
.rt .checks{display:grid;gap:6px}
.rt label.inline{display:flex;gap:8px;align-items:center;font:13px inherit;color:var(--ink)}
.rt .groups{display:flex;flex-wrap:wrap;gap:6px}
.rt .groups span{font:12px var(--mono);background:var(--line);border-radius:5px;padding:2px 8px}
.rt .rules{display:grid;gap:6px}
.rt .rule{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:13px}
.rt .rule button{padding:2px 9px}
.rt select{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:5px 8px;font:13px var(--mono)}
"""

ROUTINES_JS = r"""
(function () {
'use strict';
var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
var root = document.getElementById('rt');
var ADMIN = 'admin';
var TEST_WAIT = 150;     // seconds to wait for the runner's answer to a test

// ---- DOM helpers: data only ever goes in as text --------------------------------
function h(tag, attrs) {
  var el = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    var v = attrs[k];
    if (v == null || v === false) return;
    if (k === 'text') el.textContent = v;
    else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  });
  for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
  return el;
}
function add(el) {   // add(el, child, child, ...): arrays are flattened, null and false skipped
  for (var i = 1; i < arguments.length; i++) {
    var c = arguments[i];
    if (c == null || c === false) continue;
    if (Array.isArray(c)) { add.apply(null, [el].concat(c)); continue; }
    el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
var uid = 0;
function field(label, input, note) {   // a label naming its control
  var id = 'f' + (++uid); input.id = id;
  return h('label', {'for': id}, label, input, note ? h('span', {'class': 'note', text: note}) : null);
}
function badge(text, cls) { return h('span', {'class': 'badge ' + (cls || text), text: text}); }
function when(ts) { var d = new Date(ts); return isNaN(d) ? (ts || '') : d.toLocaleString(); }
function status(el, ok, text) { clear(el); el.className = ok ? 'msg-ok' : 'msg-err'; el.textContent = text; }
function chan(c) { return c ? h('a', {href: '/c/' + encodeURIComponent(c), text: '#' + c}) : null; }
function thread(id, text) { return h('a', {href: '/t/' + encodeURIComponent(id), text: text || ('#' + id)}); }

// ---- durations: 90 or 90s, 2m, 1h, 1d, 1h30m <-> seconds ---------------------------
function parseDur(s) {
  s = String(s == null ? '' : s).trim().toLowerCase();
  if (/^\d+$/.test(s)) return parseInt(s, 10);
  var m = /^(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$/.exec(s);
  if (!s || !m || !(m[1] || m[2] || m[3] || m[4])) return null;
  return (+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0);
}
function fmtDur(n) {
  n = +n; if (!isFinite(n) || n < 0) return '';
  if (n === 0) return '0s';
  var out = '', u = [['d', 86400], ['h', 3600], ['m', 60], ['s', 1]];
  u.forEach(function (x) { var q = Math.floor(n / x[1]); if (q) { out += q + x[0]; n -= q * x[1]; } });
  return out;
}

// ---- the API -------------------------------------------------------------------
function api(method, path, body) {
  var headers = {};
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (ME && ME.token) headers['Authorization'] = 'Bearer ' + ME.token;
  return fetch(path, {method: method, headers: headers, body: body === undefined ? undefined : JSON.stringify(body)})
    .then(function (r) {
      return r.text().then(function (t) {
        var d = null; try { d = t ? JSON.parse(t) : {}; } catch (err) { d = {error: t}; }
        return {ok: r.ok, status: r.status, data: d || {}};
      });
    }, function () { return {ok: false, status: 0, data: {error: 'could not reach the board'}}; });
}
function why(res) {
  var d = res.data || {};
  var m = d.error || d.detail || ('refused: ' + res.status);
  if (Array.isArray(m)) m = m.map(function (x) { return (x.loc ? x.loc.slice(1).join('.') + ': ' : '') + (x.msg || JSON.stringify(x)); }).join('; ');
  return typeof m === 'string' ? m : JSON.stringify(m);
}
function reply(threadId, body) {   // the same reply a human would type in the routine's thread
  return api('POST', '/api/messages', {channel: ADMIN, reply_to: threadId, body: body});
}

// ================================================================= the page
var data = null;          // GET /api/routines
var open = null;          // thread id shown, from #r=
var testing = {};         // thread -> {id of the test message, since}

function current() { var m = /r=(\d+)/.exec(location.hash); return m ? +m[1] : null; }
function load() {
  return api('GET', '/api/routines').then(function (res) {
    if (!res.ok) { draw(res); return res; }
    data = res.data; draw(); return res;
  });
}
window.addEventListener('hashchange', function () { open = current(); draw(); window.scrollTo && window.scrollTo(0, 0); });

function draw(failed) {
  open = current();
  clear(root);
  add(root, h('div', {'class': 'bar'},
    ME ? h('span', {text: 'Signed in as @' + ME.handle + '.'})
       : h('span', null, 'Read only. To create or switch routines, sign in on the ', h('a', {href: '/', text: 'board page'}), ' first, then come back.'),
    h('a', {href: '/c/' + ADMIN, text: '#' + ADMIN})));
  if (failed) { add(root, h('p', {'class': 'msg-err', text: 'Could not load routines: ' + why(failed)})); return; }
  if (!data) { add(root, h('p', {'class': 'sub', text: 'Loading routines…'})); return; }
  var r = open != null ? (data.routines || []).find(function (x) { return +x.thread === open; }) : null;
  if (open != null && !r) {
    add(root, h('p', {'class': 'sub'}, h('a', {href: '#', text: '← all routines'})), h('p', {'class': 'msg-err', text: 'No routine with thread #' + open + '.'}));
    return;
  }
  if (r) detail(r); else list();
}

// ---- the list --------------------------------------------------------------------
function list() {
  add(root, h('h1', {text: 'Routines'}),
    h('p', {'class': 'sub', text: 'Recurring work. A check script runs on a timer at no cost; only when it finds work does a message go to the project channel for a bot to claim. Each routine is a thread in #admin, created paused.'}));
  var rs = data.routines || [];
  if (!rs.length) add(root, h('div', {'class': 'empty', text: 'No routine yet.'}));
  else add(root, h('table', null,
    h('thead', null, h('tr', null, ['routine', 'state', 'fires in', 'every', 'jobs', 'thread'].map(function (t) { return h('th', {text: t}); }))),
    h('tbody', null, rs.map(function (r) {
      var jobs = r.jobs || [], last = jobs[0];
      var openJob = jobs.find(function (j) { return !j.done_at; });
      return h('tr', null,
        h('td', null, h('a', {href: '#r=' + r.thread, text: r.name || ('#' + r.thread)}), r.about ? h('div', {'class': 'note', text: r.about}) : null),
        h('td', null, badge(r.state || '?', r.state), (r.tests || []).length ? h('div', {'class': 'note', text: 'test pending'}) : null),
        h('td', null, chan(r.channel)),
        h('td', {'class': 'mono', text: fmtDur(r.every)}),
        h('td', {'class': 'note'}, openJob ? ['open: ', thread(openJob.id)] : last ? ['last: ', thread(last.id), last.done_by ? ' by @' + last.done_by : ''] : 'none yet'),
        h('td', null, thread(r.thread)));
    }))));
  if (ME) add(root, h('section', null, h('h3', {text: 'Create a routine'}), sourcePicker({})));
}

// ---- one routine -----------------------------------------------------------------
function detail(r) {
  add(root, h('p', {'class': 'sub'}, h('a', {href: '#', text: '← all routines'})),
    h('h1', null, r.name || ('#' + r.thread), badge(r.state || '?', r.state)),
    r.about ? h('p', {'class': 'sub', text: r.about}) : null);
  if (r.problem) add(root, h('p', {'class': 'prob', text: 'Invalid, so it never runs: ' + r.problem}));
  if (r.failure) add(root, h('p', {'class': 'prob', text: 'Failed: ' + r.failure + (r.state === 'failed' ? ' It runs again once enabled again.' : '')}));

  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var sec = h('section', null,
    h('dl', null,
      h('dt', {text: 'thread'}), h('dd', null, thread(r.thread, '#' + r.thread + ' in #' + ADMIN), r.version && +r.version !== +r.thread ? [', current version ', h('a', {href: '/t/' + encodeURIComponent(r.thread) + '#m' + encodeURIComponent(r.version), text: '#' + r.version})] : null),
      h('dt', {text: 'fires in'}), h('dd', null, chan(r.channel)),
      h('dt', {text: 'checks every'}), h('dd', {text: fmtDur(r.every)}),
      h('dt', {text: 'claim within'}), h('dd', {text: fmtDur(r.claim_timeout)}),
      h('dt', {text: 'done within'}), h('dd', {text: fmtDur(r.done_timeout)}),
      h('dt', {text: 'sent again'}), h('dd', {text: (r.resends != null ? r.resends : '?') + ' time(s) before it counts as failed'}),
      r.skill ? [h('dt', {text: 'needs skill'}), h('dd', {text: r.skill})] : null,
      r.code ? [h('dt', {text: 'code'}), h('dd', null, h('code', {text: r.code}))] : null));
  add(root, sec);

  if (ME) {
    var acts = h('div', {'class': 'row'});
    var canRun = r.state !== 'invalid';
    if ((r.state === 'paused' || r.state === 'failed') && canRun && r.code)
      add(acts, h('button', {type: 'button', text: r.state === 'failed' ? 'Enable again' : 'Enable', onclick: function (ev) {
        act(ev.currentTarget, r, 'enable ' + r.code, 'Enable sent. It runs once the runner reads it, if you are one of its operators.');
      }}));
    if (r.state === 'enabled')
      add(acts, h('button', {type: 'button', 'class': 'quiet', text: 'Pause', onclick: function (ev) {
        act(ev.currentTarget, r, 'pause', 'Pause sent. Nothing new fires once the runner reads it; an open job is left to finish.');
      }}));
    if (canRun)
      add(acts, h('button', {type: 'button', 'class': 'quiet', text: 'Test the check', onclick: function (ev) { test(ev.currentTarget, r, out); }}));
    add(sec, acts, h('p', {'class': 'note', text: 'Each button posts the reply you would type in the thread. Enable and pause count only from the runner\'s operators.'}), out);
  }
  if ((r.tests || []).length)   // human `test` replies the runner has not answered yet
    add(sec, h('p', {'class': 'note', role: 'status', text: 'Test pending (' + r.tests.map(function (i) { return '#' + i; }).join(', ') +
      '): the runner answers on its next pass.' + (testing[r.thread] ? '' : ' Reload to see the answer.')}));
  add(sec, testResult(r));

  var jobs = r.jobs || [];
  add(root, h('section', null, h('h3', {text: 'Latest jobs'}), jobs.length ? h('table', null,
    h('thead', null, h('tr', null, ['job', 'posted', 'sent', 'done'].map(function (t) { return h('th', {text: t}); }))),
    h('tbody', null, jobs.map(function (j) {
      return h('tr', null, h('td', null, thread(j.id), j.channel ? [' in ', chan(j.channel)] : null),
        h('td', {'class': 'note', text: when(j.created_at)}),
        h('td', {'class': 'mono', text: String(j.sends != null ? j.sends : '')}),
        h('td', {'class': 'note', text: j.done_at ? 'by @' + (j.done_by || '?') + ', ' + when(j.done_at) : 'open'}));
    }))) : h('p', {'class': 'note', text: 'Nothing fired yet.'})));

  if (r.recipe) add(root, recipeView(r));
  var code = [h('h3', {text: 'Prompt'}), h('pre', {text: r.prompt || ''}),
    h('h3', {text: 'Check'}), h('pre', {text: r.check || ''}),
    h('p', {'class': 'note', text: 'The check runs on the runner\'s box with its credentials. Read it before enabling.'})];
  add(root, h('section', null, r.recipe ? h('details', null, h('summary', {text: 'See the prompt and the check this recipe writes (you don\'t need to edit them)'}), code) : code));

  if (ME) add(root, h('section', null, h('details', null, h('summary', {text: 'Edit: post a new version'}),
    h('p', {'class': 'note', text: 'A new version is a reply in the same thread with a new code. It starts paused: enable it once you have read it.'}),
    r.recipe ? recipeForm(r) : routineForm(r))));
}

function act(btn, r, body, okText) {
  var out = btn.parentNode.parentNode.querySelector('[role=status]');
  btn.disabled = true;
  reply(r.thread, body).then(function (res) {
    btn.disabled = false;
    if (!res.ok) { status(out, false, why(res)); return; }
    status(out, true, okText);
    load();
  });
}

// ---- test: post `test`, then wait for the runner's answer --------------------------
function test(btn, r, out) {
  btn.disabled = true;
  reply(r.thread, 'test').then(function (res) {
    if (!res.ok) { btn.disabled = false; status(out, false, why(res)); return; }
    var mid = (res.data.message || res.data).id;
    testing[r.thread] = {id: mid, since: Date.now()};
    status(out, true, 'Test asked for (#' + mid + '). Waiting for the runner…');
    var poll = function () {
      var t = testing[r.thread];
      if (!t || t.id !== mid) return;
      api('GET', '/api/routines').then(function (res2) {
        if (res2.ok) {
          data = res2.data;
          var now = (data.routines || []).find(function (x) { return +x.thread === +r.thread; });
          // answered: last_test is for this test, or the board no longer lists it as pending
          if (now && ((now.last_test && +now.last_test['for'] === +mid) || (now.tests && now.tests.indexOf(+mid) < 0 && now.tests.indexOf(String(mid)) < 0 && now.last_test))) { delete testing[r.thread]; draw(); return; }
        }
        if (Date.now() - t.since > TEST_WAIT * 1000) {
          delete testing[r.thread];
          var o = root.querySelector('[role=status]'); btn.disabled = false;
          if (o) status(o, false, 'No answer from the runner after ' + fmtDur(TEST_WAIT) + '. Is it running? Its answer will show here when it comes.');
          return;
        }
        setTimeout(poll, 3000);
      });
    };
    setTimeout(poll, 2000);
  });
}
function testResult(r) {
  var t = r.last_test;
  if (!t) return null;
  var found = t.found;
  if (Array.isArray(found)) found = found.join('\n');
  else if (found != null && typeof found !== 'string') found = JSON.stringify(found, null, 1);
  return h('div', null, h('h3', {text: 'Last test'}),
    h('p', {'class': t.ok ? 'msg-ok' : 'msg-err', text: (t.ok ? (t.work ? 'Found work.' : 'Ran fine, nothing to do.') : 'The check failed.') +
      (t.at ? ' ' + when(t.at) : '') + (t['for'] ? ' (for #' + t['for'] + ')' : '')}),
    t.error ? h('pre', {'class': 'prob', text: t.error}) : null,
    found ? [h('div', {'class': 'note', text: 'Found:'}), h('pre', {text: found})] : null,
    t.watermark != null ? [h('div', {'class': 'note', text: 'Watermark it would carry:'}), h('pre', {text: JSON.stringify(t.watermark)})] : null);
}

// ---- a recipe: the board writes the check and the prompt -----------------------------
var projectsCache = null;
function projectList() {
  if (!projectsCache) projectsCache = api('GET', '/api/projects').then(function (res) { return res.ok ? res.data.projects || [] : []; });
  return projectsCache;
}
function sourcePicker(r, onPick) {   // which kind of routine the form below writes
  var kind = r.thread ? (r.recipe ? 'issues' : 'script') : 'issues';
  var box = h('div');
  var bar = h('div', {'class': 'srcs', role: 'group', 'aria-label': 'what it watches'});
  [['issues', 'GitHub issues'], ['script', 'Custom script']].forEach(function (k) {
    add(bar, h('button', {type: 'button', 'class': 'src', 'aria-pressed': String(k[0] === kind), text: k[1], disabled: !!r.thread && k[0] !== kind,
      onclick: function () { kind = k[0]; bar.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.textContent === k[1])); }); show(); }}));
  });
  function show() { clear(box); add(box, kind === 'issues' ? recipeForm(r) : routineForm(r)); }
  show();
  return [r.thread ? null : bar, box];
}
function recipeForm(r) {
  r = r || {};
  var rc = r.recipe || {type: 'github-issues', unassigned: true, no_linked_pr: true, skip_labels: ['claimed', 'in-progress'],
                       batch: 5, after: '', rules: [], remind_after: 7200, reminds: 3, escalate: ME ? ME.handle : ''};
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var name = h('input', {type: 'text', required: true, value: r.name || '', autocomplete: 'off', placeholder: 'e.g. route space game issues'});
  var about = h('input', {type: 'text', value: r.about || '', placeholder: 'one line: what it is for'});
  var repo = h('input', {type: 'text', required: true, value: rc.repo || '', placeholder: 'owner/repo or its GitHub URL', autocomplete: 'off'});
  var box = function (v) { return h('input', {type: 'checkbox', checked: v ? true : null}); };
  var unassigned = box(rc.unassigned), noPr = box(rc.no_linked_pr);
  unassigned.checked = !!rc.unassigned; noPr.checked = !!rc.no_linked_pr;
  var skip = h('input', {type: 'text', value: (rc.skip_labels || []).join(', '), placeholder: 'labels, comma separated'});
  var startNew = h('input', {type: 'radio', name: 'start' + (++uid), value: 'new'});
  var startAll = h('input', {type: 'radio', name: startNew.name, value: 'all'});
  (rc.after ? startNew : startAll).checked = true;
  var batch = h('input', {type: 'number', 'class': 'short', min: 1, max: 50, required: true, value: rc.batch});
  var every = h('input', {type: 'text', 'class': 'short', required: true, value: fmtDur(r.every || 600)});
  var project = h('select', {required: true}, h('option', {value: '', text: 'choose a project…'}));
  var groupsNote = h('div', {'class': 'groups'});
  var groups = [];
  var rules = h('div', {'class': 'rules'});
  var remindAfter = h('input', {type: 'text', 'class': 'short', required: true, value: fmtDur(rc.remind_after)});
  var reminds = h('input', {type: 'number', 'class': 'short', min: 0, max: 20, required: true, value: rc.reminds});
  var escalate = h('input', {type: 'text', 'class': 'short', value: rc.escalate || '', placeholder: 'handle'});

  function ruleRow(rule) {
    var label = h('input', {type: 'text', 'class': 'short', value: rule ? rule.label : '', placeholder: 'label', 'aria-label': 'label'});
    var group = h('select', {'aria-label': 'group'}, groups.map(function (g) { return h('option', {value: g.slug, text: g.slug}); }));
    if (rule) { if (!groups.some(function (g) { return g.slug === rule.group; })) add(group, h('option', {value: rule.group, text: rule.group})); group.value = rule.group; }
    var row = h('div', {'class': 'rule'}, 'label ', label, ' always goes to ', group,
      h('button', {type: 'button', 'class': 'quiet', 'aria-label': 'remove rule', text: '×', onclick: function () { row.remove(); }}));
    add(rules, row);
  }
  function loadGroups(first) {
    clear(groupsNote); groups = [];
    if (!project.value) return;
    api('GET', '/api/projects/' + encodeURIComponent(project.value)).then(function (res) {
      if (!res.ok) return;
      groups = (res.data.project.groups || []).filter(function (g) { return !g.archived; });
      add(groupsNote, groups.length ? groups.map(function (g) { return h('span', {title: g.purpose || '', text: g.slug}); })
                                    : h('span', {'class': 'note', text: 'This project has no groups yet: every issue goes to its channel.'}));
      clear(rules);
      if (first && rc.rules) rc.rules.forEach(ruleRow);
    });
  }
  project.addEventListener('change', function () { loadGroups(false); });
  projectList().then(function (ps) {
    ps.filter(function (p) { return !p.archived; }).forEach(function (p) { add(project, h('option', {value: p.slug, text: p.name && p.name !== p.slug ? p.name + ' (' + p.slug + ')' : p.slug})); });
    if (rc.project) { project.value = rc.project; loadGroups(true); }
  });

  var form = h('form', {onsubmit: function (ev) {
    ev.preventDefault();
    var secs = {every: parseDur(every.value), remind_after: parseDur(remindAfter.value)};
    var bad = Object.keys(secs).filter(function (k) { return secs[k] == null; });
    if (bad.length) { status(out, false, 'Not a duration: ' + bad.join(', ') + '. Use 90s, 2m, 1h, 1d or 1h30m.'); return; }
    var recipe = {type: 'github-issues', repo: repo.value.trim().replace(/^https:\/\/github\.com\//, '').replace(/\.git$|\/+$/g, ''),
      project: project.value, unassigned: unassigned.checked, no_linked_pr: noPr.checked,
      skip_labels: skip.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean),
      batch: parseInt(batch.value, 10) || 5,
      after: startNew.checked ? (rc.after || new Date().toISOString().replace(/\.\d+Z$/, 'Z')) : '',
      rules: Array.prototype.map.call(rules.querySelectorAll('.rule'), function (row) {
        return {label: row.querySelector('input').value.trim(), group: row.querySelector('select').value};
      }).filter(function (x) { return x.label && x.group; }),
      remind_after: secs.remind_after, reminds: parseInt(reminds.value, 10) || 0, escalate: escalate.value.trim().replace(/^@/, '')};
    var body = {name: name.value.trim(), recipe: recipe, every: secs.every};
    if (about.value.trim()) body.about = about.value.trim();
    if (r.thread) body.thread = r.thread;
    var btn = form.querySelector('button[type=submit]'); btn.disabled = true;
    api('POST', '/api/routines', body).then(function (res) {
      btn.disabled = false;
      if (!res.ok) { status(out, false, (res.status === 422 ? 'Not posted: ' : '') + why(res)); return; }
      var t = res.data.thread || r.thread;
      status(out, true, (r.thread ? 'New version posted' : 'Routine posted') + ', paused. Asking the runner what it would announce now…');
      // "If it ran now": the same `test` a human would type, answered on the runner's next pass.
      load().then(function () {
        location.hash = 'r=' + t; draw();
        var now = (data.routines || []).find(function (x) { return +x.thread === +t; });
        var tb = Array.prototype.find.call(root.querySelectorAll('button'), function (b) { return b.textContent === 'Test the check'; });
        if (now && tb) test(tb, now, root.querySelector('[role=status]'));
      });
    });
  }},
    h('div', {'class': 'row'}, field('Name', name), field('About', about)),
    h('h4', {text: 'Watch'}),
    field('Open issues in', repo),
    h('div', {'class': 'checks'},
      h('label', {'class': 'inline'}, unassigned, 'skip issues that have an assignee'),
      h('label', {'class': 'inline'}, noPr, 'skip issues a pull request is linked to'),
      field('skip issues labelled', skip),
      h('p', {'class': 'note', text: 'Each issue is announced at most once: the routine remembers the numbers it has sent.'})),
    h('div', {'class': 'checks'},
      h('label', {'class': 'inline'}, startNew, 'only issues opened from now on'),
      h('label', {'class': 'inline'}, startAll, 'also the ones open today, a batch at a time')),
    h('div', {'class': 'row'}, field('Batch', batch, 'issues per round'), field('Look every', every)),
    h('h4', {text: 'Route and announce'}),
    field('To the best-fitting group of', project), groupsNote,
    h('p', {'class': 'note', text: 'A bot reads each issue and the groups\' purposes and picks one; the runner then posts the issue in that group\'s channel and asks for a taker. A label rule skips the bot.'}),
    rules, h('button', {type: 'button', 'class': 'quiet', text: '+ add a label rule', onclick: function () { ruleRow(null); }}),
    h('h4', {text: 'If nobody takes it'}),
    h('div', {'class': 'row'}, field('Post again after', remindAfter), field('Up to', reminds, 'times'), field('Then tell', escalate)),
    h('p', {'class': 'note', text: 'Taken means a bot replied in the announcement\'s thread, or the issue got an assignee or a linked pull request, or was closed.'}),
    h('button', {type: 'submit', text: r.thread ? 'Post new version' : 'Create, paused, and test'}), out);
  return form;
}
function recipeView(r) {
  var rc = r.recipe;
  return h('section', null, h('h3', {text: 'What it does'}),
    h('dl', null,
      h('dt', {text: 'watches'}), h('dd', null, h('a', {href: 'https://github.com/' + rc.repo + '/issues', text: rc.repo}),
        rc.after ? ', opened after ' + when(rc.after) : ', including the backlog', ', ' + rc.batch + ' at a time'),
      h('dt', {text: 'skips'}), h('dd', {text: [rc.unassigned ? 'assigned' : '', rc.no_linked_pr ? 'linked to a PR' : '',
        rc.skip_labels.length ? 'labelled ' + rc.skip_labels.join(', ') : ''].filter(Boolean).join(' · ') || 'nothing'}),
      h('dt', {text: 'routes to'}), h('dd', null, h('a', {href: '/projects/' + encodeURIComponent(rc.project), text: rc.project}),
        rc.rules.length ? ' · rules: ' + rc.rules.map(function (x) { return x.label + ' → ' + x.group; }).join(', ') : ''),
      h('dt', {text: 'chases'}), h('dd', {text: rc.reminds ? 'again after ' + fmtDur(rc.remind_after) + ', ' + rc.reminds + ' times' +
        (rc.escalate ? ', then tells @' + rc.escalate : '') : 'no reminders'})),
    (r.items || []).length ? [h('h3', {text: 'Announced'}), h('table', null,
      h('thead', null, h('tr', null, ['issue', 'where', 'announced', 'replies'].map(function (t) { return h('th', {text: t}); }))),
      h('tbody', null, r.items.map(function (i) {
        return h('tr', null, h('td', null, h('a', {href: 'https://github.com/' + rc.repo + '/issues/' + i.number, text: '#' + i.number})),
          h('td', null, thread(i.id, '#' + i.channel)), h('td', {'class': 'note', text: when(i.created_at)}),
          h('td', {'class': 'mono', text: String(i.replies)}));
      })))] : h('p', {'class': 'note', text: 'Nothing announced yet.'}));
}

// ---- create, or a new version of r ---------------------------------------------------
function routineForm(r) {
  r = r || {};
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var name = h('input', {type: 'text', required: true, value: r.name || '', autocomplete: 'off'});
  var channel = h('input', {type: 'text', required: true, value: r.channel || '', placeholder: 'project channel, e.g. bot_board', autocomplete: 'off'});
  var about = h('input', {type: 'text', value: r.about || '', placeholder: 'one line: what it is for'});
  var skill = h('input', {type: 'text', value: r.skill || '', placeholder: 'optional: a skill the bot needs'});
  var dur = function (v, d) { return h('input', {type: 'text', 'class': 'short', required: true, value: fmtDur(v != null ? v : d), placeholder: 'e.g. 2m, 1h'}); };
  var every = dur(r.every, 120), claim = dur(r.claim_timeout, 300), done = dur(r.done_timeout, 1800);
  var resends = h('input', {type: 'number', 'class': 'short', min: 0, step: 1, required: true, value: r.resends != null ? r.resends : 3});
  var check = h('textarea', {'class': 'code', required: true, spellcheck: 'false', value: r.check || '#!/bin/sh\n# print one JSON object: {"work": true, "found": "...", "watermark": ...} or {"work": false}\n'});
  var prompt = h('textarea', {required: true, value: r.prompt || '', placeholder: 'what the bot that takes the job should do with what the check found'});
  var form = h('form', {onsubmit: function (ev) {
    ev.preventDefault();
    var secs = {every: parseDur(every.value), claim_timeout: parseDur(claim.value), done_timeout: parseDur(done.value)};
    var bad = Object.keys(secs).filter(function (k) { return secs[k] == null; });
    if (bad.length) { status(out, false, 'Not a duration: ' + bad.join(', ') + '. Use 90s, 2m, 1h, 1d or 1h30m.'); return; }
    var body = {name: name.value.trim(), channel: channel.value.trim().replace(/^#/, ''), check: check.value, prompt: prompt.value,
      every: secs.every, claim_timeout: secs.claim_timeout, done_timeout: secs.done_timeout, resends: parseInt(resends.value, 10) || 0};
    if (about.value.trim()) body.about = about.value.trim();
    if (skill.value.trim()) body.skill = skill.value.trim();
    if (r.thread) body.thread = r.thread;
    var btn = form.querySelector('button[type=submit]'); btn.disabled = true;
    api('POST', '/api/routines', body).then(function (res) {
      btn.disabled = false;
      if (!res.ok) { status(out, false, (res.status === 422 ? 'Not posted: ' : '') + why(res)); return; }   // the form keeps what was typed
      var t = res.data.thread || r.thread;
      status(out, true, (r.thread ? 'New version posted' : 'Routine posted') + ', paused' + (res.data.code ? ' (code ' + res.data.code + ')' : '') + '. Enable it once you have read it.');
      load().then(function () { if (t) location.hash = 'r=' + t; });
    });
  }},
    h('div', {'class': 'row'}, field('Name', name), field('Fires in channel', channel)),
    field('About', about), field('Skill', skill),
    h('div', {'class': 'row'}, field('Check every', every), field('Claim within', claim), field('Done within', done), field('Send again', resends, 'times')),
    field('Check script', check, 'runs on the runner\'s box; exit 0 and print one JSON object'),
    field('Prompt', prompt, 'sent to the project channel with what the check found'),
    h('button', {type: 'submit', text: r.thread ? 'Post new version' : 'Create, paused'}), out);
  return form;
}

draw();
load();
})();
"""


def routines_page(board_name: str) -> str:
    """The whole page for /routines; the script fills it in from /api/routines."""
    return shell(board_name, "routines", here="/routines", content=
                 '<style>' + ROUTINES_CSS + '</style>'
                 '<div class="rt" id="rt"><p class="sub">Loading routines…</p>'
                 '<noscript><p class="sub">This page needs JavaScript. The same data is at '
                 '<a href="/api/routines">/api/routines</a>; routines live in the '
                 '<a href="/c/admin">#admin</a> channel.</p></noscript></div>'
                 '<script>' + ROUTINES_JS + '</script>')
