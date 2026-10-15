"""The routines page: what recurring work exists, and a way to delete it.

A routine is a thread in admin.routine (bin/routines): a human asks there, the
admin bot works it out with them, tests it and switches it on. This page is a
window onto those threads, from GET /api/routines; its one write is Delete
(DELETE /api/routines/{thread}), with the identity the board page keeps in this
browser (localStorage bb.me). Creating, changing, pausing and testing happen in
the thread. Data from the API is only ever put on the page as text, never as HTML.
"""
from __future__ import annotations

import json

from .routines import ADMIN
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
var ADMIN = '__ADMIN__';

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
function badge(text, cls) { return h('span', {'class': 'badge ' + (cls || text), text: text}); }
function when(ts) { var d = new Date(ts); return isNaN(d) ? (ts || '') : d.toLocaleString(); }
function status(el, ok, text) { clear(el); el.className = ok ? 'msg-ok' : 'msg-err'; el.textContent = text; }
function chan(c) { return c ? h('a', {href: '/c/' + encodeURIComponent(c), text: '#' + c}) : null; }
function thread(id, text) { return h('a', {href: '/t/' + encodeURIComponent(id), text: text || ('#' + id)}); }

// ---- durations: seconds -> 90s, 2m, 1h, 1d, 1h30m ---------------------------------
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

// ================================================================= the page
var data = null;          // GET /api/routines
var open = null;          // thread id shown, from #r=

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
       : h('span', null, 'Read only. To delete a routine, sign in on the ', h('a', {href: '/', text: 'board page'}), ' first, then come back.'),
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
    h('p', {'class': 'sub'}, 'Recurring work. A check script runs on a timer at no cost; only when it finds work does a message go to the project channel for a bot to claim. ',
      'To get a new routine, or to change one, say what you want in ', chan(ADMIN),
      ': the admin bot works it out with you there, tests it once you agree, and switches it on.'));
  var rs = data.routines || [];
  if (!rs.length) add(root, h('div', {'class': 'empty', text: 'No routine yet.'}));
  else add(root, h('table', null,
    h('thead', null, h('tr', null, ['routine', 'state', 'fires in', 'every', 'jobs', 'thread'].map(function (t) { return h('th', {text: t}); }))),
    h('tbody', null, rs.map(function (r) {
      var jobs = r.jobs || [], last = jobs[0];
      var openJob = jobs.find(function (j) { return !j.done_at; });
      return h('tr', null,
        h('td', null, h('a', {href: '#r=' + r.thread, text: r.name || ('#' + r.thread)}), r.about ? h('div', {'class': 'note', text: r.about}) : null),
        h('td', null, badge(r.state || '?', r.state)),
        h('td', null, chan(r.channel)),
        h('td', {'class': 'mono', text: fmtDur(r.every)}),
        h('td', {'class': 'note'}, openJob ? ['open: ', thread(openJob.id)] : last ? ['last: ', thread(last.id), last.done_by ? ' by @' + last.done_by : ''] : 'none yet'),
        h('td', null, thread(r.thread)));
    }))));
}

// ---- one routine -----------------------------------------------------------------
function detail(r) {
  add(root, h('p', {'class': 'sub'}, h('a', {href: '#', text: '← all routines'})),
    h('h1', null, r.name || ('#' + r.thread), badge(r.state || '?', r.state)),
    r.about ? h('p', {'class': 'sub', text: r.about}) : null);
  if (r.problem) add(root, h('p', {'class': 'prob', text: 'Invalid, so it never runs: ' + r.problem}));
  if (r.failure) add(root, h('p', {'class': 'prob', text: 'Failed: ' + r.failure + (r.state === 'failed' ? ' It runs again once enabled again.' : '')}));

  var sec = h('section', null,
    h('dl', null,
      h('dt', {text: 'thread'}), h('dd', null, thread(r.thread, '#' + r.thread + ' in #' + ADMIN), r.version && +r.version !== +r.thread ? [', current version ', h('a', {href: '/t/' + encodeURIComponent(r.thread) + '#m' + encodeURIComponent(r.version), text: '#' + r.version})] : null),
      h('dt', {text: 'fires in'}), h('dd', null, chan(r.channel)),
      h('dt', {text: 'checks every'}), h('dd', {text: fmtDur(r.every)}),
      h('dt', {text: 'claim within'}), h('dd', {text: fmtDur(r.claim_timeout)}),
      h('dt', {text: 'done within'}), h('dd', {text: fmtDur(r.done_timeout)}),
      h('dt', {text: 'sent again'}), h('dd', {text: (r.resends != null ? r.resends : '?') + ' time(s) before it counts as failed'}),
      r.skill ? [h('dt', {text: 'needs skill'}), h('dd', {text: r.skill})] : null),
    h('p', {'class': 'note'}, 'To change, pause or test it, say so in ', thread(r.thread, 'its thread'), '.'));
  add(root, sec);
  if (ME) {
    var out = h('div', {role: 'status', 'aria-live': 'polite'});
    add(sec, h('button', {type: 'button', 'class': 'quiet', text: 'Delete', onclick: function (ev) {
      if (!window.confirm('Delete the routine ' + (r.name || '#' + r.thread) + '? It stops for good; its thread stays as history.')) return;
      var btn = ev.currentTarget; btn.disabled = true;
      api('DELETE', '/api/routines/' + encodeURIComponent(r.thread)).then(function (res) {
        btn.disabled = false;
        if (!res.ok) { status(out, false, why(res)); return; }
        location.hash = ''; load();
      });
    }}), out);
  }
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
    h('p', {'class': 'note', text: 'The check runs on the runner\'s box with its credentials.'})];
  add(root, h('section', null, r.recipe ? h('details', null, h('summary', {text: 'See the prompt and the check this recipe writes'}), code) : code));
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
                 f'<a href="/c/{ADMIN}">#{ADMIN}</a> channel.</p></noscript></div>'
                 '<script>' + ROUTINES_JS.replace("'__ADMIN__'", json.dumps(ADMIN)) + '</script>')
