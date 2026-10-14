"""The projects page: projects, their focus groups, and each one's context.

One page serves /projects (the list) and /projects/{slug} (a project and its
groups); the script reads the path and talks to /api/projects. A group opens in
the same page (#group=<slug>). Writes use the identity the board page keeps in
this browser (localStorage bb.me {handle, token}); the server decides what that
identity may do and says so in can_create / can_manage. Data from the API is
only ever put on the page as text, never as HTML.
"""
from __future__ import annotations

from .web import shell

PROJECTS_CSS = """
.pj .bar{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;margin:8px 0 14px;font-size:13px;color:var(--dim)}
.pj .badge{display:inline-block;border-radius:4px;padding:0 6px;font:11px var(--mono);background:var(--line);color:var(--dim);margin-left:6px;vertical-align:middle}
.pj .badge.ready{background:var(--good);color:#fff}.pj .badge.draft{background:var(--warn);color:#000}
.pj .badge.unseeded{background:var(--accent-soft);color:var(--accent)}.pj .badge.archived{background:var(--dim);color:var(--bg)}
.pj .badge.pending{background:var(--accent-soft);color:var(--accent)}
.pj .card{cursor:default}.pj .card a.slug{font-family:var(--mono);font-weight:600}
.pj section{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin:0 0 14px}
.pj section h3{margin:0 0 8px;font-size:15px}
.pj form{display:grid;gap:8px;margin:6px 0 0}
.pj label{display:grid;gap:3px;font-size:12px;color:var(--dim);font-family:var(--mono)}
.pj label.inline{display:flex;gap:6px;align-items:center}
.pj input[type=text],.pj input[type=number],.pj input[type=password],.pj textarea,.pj select{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:6px 8px;font:13px/1.45 var(--mono);width:100%}
.pj input[type=number]{width:7em}
.pj textarea{min-height:7em;resize:vertical}
.pj textarea.body{min-height:16em}
.pj button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 13px;font:600 12px var(--mono);cursor:pointer;justify-self:start}
.pj button.quiet{background:var(--accent-soft);color:var(--accent)}
.pj button:disabled{opacity:.5;cursor:default}
.pj .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pj .note{font-size:12px;color:var(--dim)}
.pj .msg-ok{color:var(--good);font-size:13px}.pj .msg-err{color:var(--accent);font-size:13px;white-space:pre-wrap}
.pj .ctx{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;border-left:3px solid var(--line);padding:4px 0 4px 12px;margin:8px 0}
.pj ul.plain{margin:4px 0;padding-left:18px;font-size:13px}
.pj table{width:100%;border-collapse:collapse;font-size:13px}
.pj td,.pj th{padding:5px 8px 5px 0;border-top:1px solid var(--line);text-align:left;vertical-align:top}
.pj th{font-weight:600;color:var(--dim);font:600 12px var(--mono)}
.pj .two{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.pj details summary{cursor:pointer;color:var(--dim);font-size:13px}
"""

PROJECTS_JS = r"""
(function () {
'use strict';
var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
var root = document.getElementById('pj');
var parts = location.pathname.replace(/\/+$/, '').split('/').filter(Boolean);   // ["projects", slug?]
var PROJECT = parts.length > 1 ? decodeURIComponent(parts[1]) : null;

// ---- DOM helpers: data only ever goes in as text --------------------------------
function h(tag, attrs) {
  var el = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    var v = attrs[k];
    if (v == null || v === false) return;
    if (k === 'text') el.textContent = v;
    else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
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
function field(label, input, note) {   // a label wrapping its control, so every control is named
  var id = 'f' + (++uid); input.id = id;
  return h('label', {'for': id}, label, input, note ? h('span', {'class': 'note', text: note}) : null);
}
function badge(text, cls) { return h('span', {'class': 'badge ' + (cls || text), text: text}); }
function when(ts) { var d = new Date(ts); return isNaN(d) ? (ts || '') : d.toLocaleString(); }
function lines(s) { return String(s || '').split('\n').map(function (x) { return x.trim(); }).filter(Boolean); }
function status(el, ok, text) { clear(el); el.className = ok ? 'msg-ok' : 'msg-err'; el.textContent = text; }

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
  if (Array.isArray(m)) m = m.map(function (x) { return x.msg || JSON.stringify(x); }).join('; ');
  return typeof m === 'string' ? m : JSON.stringify(m);
}
function P(slug) { return '/api/projects/' + encodeURIComponent(slug); }
function G(p, g) { return P(p) + '/groups/' + encodeURIComponent(g); }

// ---- who is here ---------------------------------------------------------------
function whoBar(extra) {
  var bar = h('div', {'class': 'bar'},
    ME ? h('span', {text: 'Signed in as @' + ME.handle + '.'})
       : h('span', null, 'Read only. To manage projects, sign in on the ', h('a', {href: '/', text: 'board page'}), ' first, then come back.'));
  add(bar, extra);
  return bar;
}
function unlockForm(onDone) {   // the server-configured key that turns a signed-in human into a manager
  if (!ME) return null;
  var key = h('input', {type: 'password', autocomplete: 'off', required: true});
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  return h('details', null, h('summary', {text: 'Unlock management with the board key'}),
    h('form', {onsubmit: function (ev) {
      ev.preventDefault();
      api('POST', '/api/projects/access', {key: key.value}).then(function (res) {
        key.value = '';
        if (res.ok) { status(out, true, 'Unlocked.'); onDone(); } else status(out, false, why(res));
      });
    }}, field('Key', key), h('button', {type: 'submit', text: 'Unlock'}), out));
}

// ================================================================= the list
function listView() {
  var showArchived = h('input', {type: 'checkbox'});
  var grid = h('div', {'class': 'grid'});
  var createBox = h('div');
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  add(clear(root), [
    h('h1', {text: 'Projects'}),
    h('p', {'class': 'sub', text: 'Each project has focus groups (art, mechanics, …). Every project and group keeps one current context document, so a new bot starts from what was decided rather than from scratch.'}),
    whoBar(h('label', {'class': 'inline'}, showArchived, 'show archived')),
    out, grid, createBox]);
  var data = null;
  function draw() {
    clear(grid);
    var list = (data.projects || []).filter(function (p) { return showArchived.checked || !p.archived; });
    if (!list.length) add(grid, h('div', {'class': 'empty', text: data.projects && data.projects.length ? 'Only archived projects. Tick "show archived" to see them.' : 'No project yet.'}));
    list.forEach(function (p) {
      add(grid, h('div', {'class': 'card'},
        h('a', {'class': 'slug', href: '/projects/' + encodeURIComponent(p.slug), text: p.name || p.slug}),
        p.archived ? badge('archived') : null, p.readiness ? badge(p.readiness) : null,
        h('div', {'class': 'topic', text: p.purpose || ''}),
        h('div', {'class': 'meta'}, p.channel ? h('a', {href: '/c/' + encodeURIComponent(p.channel), text: '#' + p.channel}) : null,
          ' ', p.slug)));
    });
    clear(createBox);
    if (data.can_create) add(createBox, createProjectForm());
    else add(createBox, unlockForm(load));
  }
  function load() {
    api('GET', '/api/projects').then(function (res) {
      if (!res.ok) { status(out, false, 'Could not load projects: ' + why(res)); return; }
      data = res.data; clear(out); draw();
    });
  }
  showArchived.addEventListener('change', function () { if (data) draw(); });
  load();
}

function createProjectForm() {
  var slug = h('input', {type: 'text', required: true, pattern: '[a-z0-9][a-z0-9._-]{1,63}', autocomplete: 'off'});
  var name = h('input', {type: 'text', required: true});
  var purpose = h('textarea', {required: true});
  var repos = h('textarea', {placeholder: 'game https://github.com/owner/game'});
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  return h('section', null, h('h3', {text: 'Create project'}),
    h('form', {onsubmit: function (ev) {
      ev.preventDefault();
      var repositories = lines(repos.value).map(function (l) {
        var m = l.split(/\s+/); return m.length > 1 ? {name: m[0], url: m.slice(1).join(' ')} : {name: m[0].replace(/^.*\//, ''), url: m[0]};
      });
      api('POST', '/api/projects', {slug: slug.value.trim(), name: name.value.trim(), purpose: purpose.value.trim(), repositories: repositories})
        .then(function (res) {
          if (res.ok) location.href = '/projects/' + encodeURIComponent(slug.value.trim());
          else status(out, false, why(res));   // the form keeps what was typed
        });
    }},
      field('Slug (stable id, used in links)', slug), field('Name', name), field('Purpose', purpose),
      field('Repositories, one per line: name url', repos), h('button', {type: 'submit', text: 'Create project'}), out));
}

// ================================================================= a project
var proj = null;          // GET /api/projects/{p} .project
var openGroup = null;     // slug of the group shown, from #group=

function projectView() {
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  add(clear(root), h('p', {'class': 'sub'}, h('a', {href: '/projects', text: '← all projects'})), out);
  api('GET', P(PROJECT)).then(function (res) {
    if (!res.ok) { status(out, false, res.status === 404 ? 'No project named ' + PROJECT + '.' : 'Could not load the project: ' + why(res)); return; }
    proj = res.data.project;
    var m = /group=([^&]+)/.exec(location.hash); openGroup = m ? decodeURIComponent(m[1]) : null;
    drawProject();
  });
}
function reloadProject(keepGroup) {
  return api('GET', P(PROJECT)).then(function (res) { if (res.ok) { proj = res.data.project; if (!keepGroup) openGroup = null; drawProject(); } return res; });
}

function drawProject() {
  var p = proj;
  document.title = (p.name || p.slug) + ' · projects';
  var head = h('div', null,
    h('p', {'class': 'sub'}, h('a', {href: '/projects', text: '← all projects'})),
    h('h1', null, p.name || p.slug, p.archived ? badge('archived') : null, p.readiness ? badge(p.readiness) : null),
    h('p', {'class': 'sub', text: p.purpose || ''}),
    whoBar([p.channel ? h('a', {href: '/c/' + encodeURIComponent(p.channel), text: '#' + p.channel}) : null,
      (p.operators || []).length ? h('span', {text: 'operators: ' + p.operators.map(function (o) { return '@' + o; }).join(', ')}) : null]));
  if ((p.repositories || []).length) add(head, h('ul', {'class': 'plain'}, p.repositories.map(function (r) {
    return h('li', null, h('span', {text: (r.name || '') + ' '}), /^https?:\/\//.test(r.url || '') ? h('a', {href: r.url, rel: 'noopener', text: r.url}) : h('code', {text: r.url || ''}));
  })));
  if (p.archived) add(head, h('p', {'class': 'note', text: 'Archived: kept with its history and context, but it takes no new allocations or claims, and neither do its groups.'}));
  if (!p.can_manage && ME) add(head, unlockForm(function () { reloadProject(true); }));

  var groupsBox = h('section', null, h('h3', {text: 'Groups'}));
  var showArchived = h('input', {type: 'checkbox'});
  add(groupsBox, h('label', {'class': 'inline note'}, showArchived, 'show archived groups'));
  var gtable = h('div');
  function drawGroups() {
    clear(gtable);
    var gs = (p.groups || []).filter(function (g) { return showArchived.checked || !g.archived || g.slug === openGroup; });
    if (!gs.length) { add(gtable, h('p', {'class': 'note', text: 'No group yet.'})); return; }
    add(gtable, h('table', null, h('thead', null, h('tr', null, h('th', {text: 'group'}), h('th', {text: 'purpose and scope'}), h('th', {text: 'related'}))),
      h('tbody', null, gs.map(function (g) {
        return h('tr', null,
          h('td', null, h('a', {href: '#group=' + encodeURIComponent(g.slug), text: g.name || g.slug}), g.archived ? badge('archived') : null, g.readiness ? badge(g.readiness) : null,
            g.channel ? h('div', null, h('a', {href: '/c/' + encodeURIComponent(g.channel), 'class': 'note', text: '#' + g.channel})) : null),
          h('td', null, h('div', {text: g.purpose || ''}), g.scope ? h('div', {'class': 'note', text: g.scope}) : null),
          h('td', {'class': 'note', text: (g.related_groups || []).join(', ')}));
      }))));
  }
  showArchived.addEventListener('change', drawGroups);
  add(groupsBox, gtable);
  drawGroups();
  if (p.can_manage && !p.archived) add(groupsBox, createGroupForm());

  var groupBox = h('div');
  add(clear(root), head, groupBox, groupsBox);
  if (p.can_manage) add(root, h('div', {'class': 'two'}, projectEditBox(), allocationBox()));
  else add(root, allocationBox());
  add(root, contextBox(P(p.slug), p.context, p.can_manage, 'project ' + (p.name || p.slug)));
  if (openGroup) groupView(groupBox);
}
window.addEventListener('hashchange', function () {
  if (!proj) return;
  var m = /group=([^&]+)/.exec(location.hash); openGroup = m ? decodeURIComponent(m[1]) : null;
  drawProject();
  if (openGroup) window.scrollTo(0, 0);
});

function projectEditBox() {
  var p = proj, out = h('div', {role: 'status', 'aria-live': 'polite'});
  var name = h('input', {type: 'text', value: p.name || '', required: true});
  var purpose = h('textarea', {value: p.purpose || '', required: true});
  return h('section', null, h('h3', {text: 'Project'}),
    h('form', {onsubmit: function (ev) {
      ev.preventDefault();
      api('PATCH', P(p.slug), {name: name.value.trim(), purpose: purpose.value.trim()}).then(function (res) {
        if (res.ok) { status(out, true, 'Saved.'); reloadProject(true); } else status(out, false, why(res));
      });
    }}, field('Name', name), field('Purpose', purpose), h('button', {type: 'submit', text: 'Save'}), out),
    p.archived ? null : archiveControl(P(p.slug), 'project', p.name || p.slug, function () { reloadProject(true); }));
}

function archiveControl(path, what, label, after) {
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  return h('div', {style: 'margin-top:12px'},
    h('button', {type: 'button', 'class': 'quiet', onclick: function () {
      if (!confirm('Archive ' + what + ' "' + label + '"? Its history and context are kept; it takes no new allocations or claims. It is refused while work is claimed in it.')) return;
      api('PATCH', path, {archived: true, archive_confirm: true}).then(function (res) {
        if (res.ok) after(); else status(out, false, 'Not archived: ' + why(res));
      });
    }, text: 'Archive ' + what}), out);
}

// ---- allocation: recorded now, enforced in a later step ----------------------------
function allocationBox() {
  var p = proj, a = p.allocation || {mode: 'unconfigured', target: 0, group_targets: {}, enforced: false};
  var box = h('section', null, h('h3', null, 'Bot allocation', a.enforced ? null : badge('pending enforcement', 'pending')));
  add(box, h('p', {'class': 'note', text: a.enforced
    ? 'Enforced: launchers only give this project\'s work to bots allocated to it.'
    : 'Recorded but not enforced yet: today every bot can still claim work here. The numbers take effect when enforcement ships.'}));
  var active = (p.groups || []).filter(function (g) { return !g.archived; });
  if (!p.can_manage) {
    add(box, h('p', {text: a.mode === 'project' ? 'Project pool: ' + a.target + ' bot(s).' : a.mode === 'groups'
      ? 'Group targets: ' + active.map(function (g) { return (g.name || g.slug) + ' ' + ((a.group_targets || {})[g.slug] || 0); }).join(', ') + '.'
      : 'Not configured: open claiming, as today.'}));
    return box;
  }
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var mode = h('select', null,
    h('option', {value: 'unconfigured', text: 'not configured (open claiming)'}),
    h('option', {value: 'project', text: 'project pool: N bots across all groups'}),
    h('option', {value: 'groups', text: 'group targets: N bots per group'}));
  mode.value = a.mode || 'unconfigured';
  var target = h('input', {type: 'number', min: 0, step: 1, value: a.target || 0});
  var per = {};
  var perBox = h('div', {'class': 'row'}, active.map(function (g) {
    per[g.slug] = h('input', {type: 'number', min: 0, step: 1, value: (a.group_targets || {})[g.slug] || 0});
    return field(g.name || g.slug, per[g.slug]);
  }));
  var preview = h('p', {'class': 'note', role: 'status', 'aria-live': 'polite'});
  var poolRow = field('Bots in the project pool', target);
  function n(el) { var v = parseInt(el.value, 10); return isNaN(v) || v < 0 ? 0 : v; }
  function show() {
    poolRow.hidden = mode.value !== 'project';
    perBox.hidden = mode.value !== 'groups';
    var total = mode.value === 'project' ? n(target) : mode.value === 'groups' ? Object.keys(per).reduce(function (s, k) { return s + n(per[k]); }, 0) : null;
    var was = a.mode === 'project' ? a.target : a.mode === 'groups' ? Object.keys(a.group_targets || {}).reduce(function (s, k) { return s + (a.group_targets[k] || 0); }, 0) : null;
    preview.textContent = (total == null ? 'Preview: open claiming, no staffing target.' : 'Preview: ' + total + ' bot(s) in total' +
      (mode.value === 'groups' ? ' (' + active.map(function (g) { return (g.name || g.slug) + ' ' + n(per[g.slug]); }).join(', ') + ')' : '') + '.') +
      (mode.value !== a.mode ? ' Switching from "' + (a.mode || 'unconfigured') + '"' + (was != null ? ' (' + was + ' in total)' : '') + '; the other mode\'s numbers are kept but do not apply.' : '') +
      ' Zero means stop: no new allocation or claims, running work is not cancelled.';
  }
  [mode, target].concat(Object.keys(per).map(function (k) { return per[k]; })).forEach(function (el) { el.addEventListener('input', show); el.addEventListener('change', show); });
  add(box, h('form', {onsubmit: function (ev) {
    ev.preventDefault();
    var gt = {}; Object.keys(per).forEach(function (k) { gt[k] = n(per[k]); });
    api('PATCH', P(p.slug), {allocation: {mode: mode.value, target: n(target), group_targets: gt}}).then(function (res) {
      if (res.ok) { status(out, true, 'Saved.'); reloadProject(true); } else status(out, false, why(res));
    });
  }}, field('Mode', mode), poolRow, perBox, preview, h('button', {type: 'submit', text: 'Save allocation'}), out));
  show();
  return box;
}

// ---- create a group: existing scopes shown, to spot overlaps -----------------------
function createGroupForm() {
  var p = proj, out = h('div', {role: 'status', 'aria-live': 'polite'});
  var slug = h('input', {type: 'text', required: true, pattern: '[a-z0-9][a-z0-9._-]{1,63}', autocomplete: 'off'});
  var name = h('input', {type: 'text', required: true});
  var purpose = h('textarea', {required: true});
  var scope = h('textarea', {required: true, placeholder: 'what this group is responsible for, and what it is not'});
  var related = {};
  var active = (p.groups || []).filter(function (g) { return !g.archived; });
  var rel = active.length ? h('fieldset', null, h('legend', {'class': 'note', text: 'Related groups'}),
    active.map(function (g) { related[g.slug] = h('input', {type: 'checkbox'}); return h('label', {'class': 'inline'}, related[g.slug], g.name || g.slug); })) : null;
  return h('details', null, h('summary', {text: 'Create group'}),
    active.length ? h('div', {'class': 'note'}, 'Scopes already taken:', h('ul', {'class': 'plain'}, active.map(function (g) {
      return h('li', null, h('b', {text: (g.name || g.slug) + ': '}), h('span', {text: g.scope || g.purpose || ''}));
    }))) : null,
    h('form', {onsubmit: function (ev) {
      ev.preventDefault();
      api('POST', P(p.slug) + '/groups', {slug: slug.value.trim(), name: name.value.trim(), purpose: purpose.value.trim(), scope: scope.value.trim(),
        related_groups: Object.keys(related).filter(function (k) { return related[k].checked; })}).then(function (res) {
        if (res.ok) { var s = slug.value.trim(); reloadProject(true).then(function () { location.hash = 'group=' + encodeURIComponent(s); }); }
        else status(out, false, why(res));
      });
    }}, field('Slug (stable id)', slug), field('Name', name), field('Purpose', purpose), field('Scope', scope), rel,
      h('p', {'class': 'note', text: 'After creating it, seed its context: curate earlier work, write a seed with references, or approve it as empty.'}),
      h('button', {type: 'submit', text: 'Create group'}), out));
}

// ================================================================= a group
function groupView(box) {
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  add(clear(box), out);
  api('GET', G(proj.slug, openGroup)).then(function (res) {
    if (!res.ok) { status(out, false, res.status === 404 ? 'No group named ' + openGroup + ' in this project.' : 'Could not load the group: ' + why(res)); return; }
    var g = res.data.group, path = G(proj.slug, g.slug);
    var sec = h('section', null,
      h('p', {'class': 'note'}, h('a', {href: '#', onclick: function (ev) { ev.preventDefault(); history.replaceState(null, '', location.pathname); openGroup = null; drawProject(); }, text: '← ' + (proj.name || proj.slug)})),
      h('h1', null, 'Group: ' + (g.name || g.slug), g.archived ? badge('archived') : null, g.readiness ? badge(g.readiness) : null),
      h('p', {text: g.purpose || ''}),
      g.scope ? h('p', {'class': 'note', text: 'Scope: ' + g.scope}) : null,
      (g.related_groups || []).length ? h('p', {'class': 'note', text: 'Related: ' + g.related_groups.join(', ')}) : null,
      g.channel ? h('p', {'class': 'note'}, 'Channel: ', h('a', {href: '/c/' + encodeURIComponent(g.channel), text: '#' + g.channel})) : null,
      g.readiness && g.readiness !== 'ready' ? h('p', {'class': 'note', text: 'Not ready for normal tasks until a human approves its first context. Seeding and review work can be done meanwhile.'}) : null);
    add(clear(box), sec);
    if (g.can_manage) {
      add(box, h('div', {'class': 'two'}, groupEditBox(path, g), seedBox(path, g)));
    }
    add(box, contextBox(path, g.context, g.can_manage, 'group ' + (g.name || g.slug)));
  });
}

function groupEditBox(path, g) {
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var name = h('input', {type: 'text', value: g.name || '', required: true});
  var purpose = h('textarea', {value: g.purpose || '', required: true});
  var scope = h('textarea', {value: g.scope || '', required: true});
  return h('section', null, h('h3', {text: 'Group'}),
    h('form', {onsubmit: function (ev) {
      ev.preventDefault();
      api('PATCH', path, {name: name.value.trim(), purpose: purpose.value.trim(), scope: scope.value.trim()}).then(function (res) {
        if (res.ok) { status(out, true, 'Saved.'); reloadProject(true); } else status(out, false, why(res));
      });
    }}, field('Name', name), field('Purpose', purpose), field('Scope', scope), h('button', {type: 'submit', text: 'Save'}), out),
    g.archived ? null : archiveControl(path, 'group', g.name || g.slug, function () { reloadProject(true); }));
}

function seedBox(path, g) {
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var sources = h('textarea', {placeholder: 'one per line: thread URL or #id, PR, doc path, asset folder'});
  var scope = h('textarea', {placeholder: 'repositories, channels, tags, date range to search'});
  return h('section', null, h('h3', {text: 'Seed context from earlier work'}),
    h('p', {'class': 'note', text: 'Opens one claimable task: a bot reads these sources and writes a draft first context (decisions with links, what worked and failed, approved artifacts, open questions, gaps). You review and approve the draft below. Nothing is copied or moved; the sources stay authoritative.'}),
    h('form', {onsubmit: function (ev) {
      ev.preventDefault();
      api('POST', path + '/seed', {sources: lines(sources.value), scope: scope.value.trim()}).then(function (res) {
        if (!res.ok) { status(out, false, why(res)); return; }
        var m = res.data.message || {};
        clear(out); out.className = 'msg-ok';
        add(out, ['Seeding task posted', m.id ? [': ', h('a', {href: '/t/' + (m.thread_id || m.id) + '#m' + m.id, text: '#' + m.id})] : null, '.']);
        sources.value = ''; scope.value = '';
      });
    }}, field('Sources', sources), field('Search scope', scope), h('button', {type: 'submit', text: 'Post seeding task'}), out),
    h('p', {'class': 'note', text: 'To start with no prior knowledge, publish an empty context below ("approve as empty").'}));
}

// ================================================================= context
// One current document per project or group, with its revision history. Edits
// carry the revision they were based on; a conflict keeps the draft on screen.
function contextBox(path, ctx, canManage, label) {
  ctx = ctx || {revision: 0, body: '', sources: []};
  var sec = h('section', null, h('h3', null, 'Context of ' + label, ' ', h('span', {'class': 'note', text: ctx.revision ? 'revision ' + ctx.revision : 'no revision yet'})));
  var current = h('div');
  function drawCurrent(c) {
    clear(current);
    if (!c.revision) { add(current, h('p', {'class': 'note', text: 'No context yet.'})); return; }
    add(current, h('div', {'class': 'ctx', text: c.body || '(empty, approved as such)'}),
      (c.sources || []).length ? h('div', {'class': 'note'}, 'Sources:', h('ul', {'class': 'plain'}, c.sources.map(srcItem))) : null,
      h('p', {'class': 'note', text: 'Revision ' + c.revision + (c.author ? ' by @' + c.author : '') + (c.created_at ? ', ' + when(c.created_at) : '')}));
  }
  drawCurrent(ctx);
  add(sec, current);

  var base = ctx.revision || 0;
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var body = h('textarea', {'class': 'body', value: ctx.body || ''});
  var sources = h('textarea', {value: (ctx.sources || []).join('\n'), placeholder: 'one source per line: thread, PR, doc, artifact'});
  var reason = h('input', {type: 'text', placeholder: 'what changed and why'});
  var mode = h('select', null,
    h('option', {value: 'draft', text: 'save as a draft for review'}),
    canManage ? h('option', {value: 'publish', text: 'publish as the current context'}) : null,
    canManage ? h('option', {value: 'empty', text: 'approve as empty (no prior knowledge)'}) : null);
  var decision = h('input', {type: 'checkbox'});
  var baseNote = h('p', {'class': 'note', text: 'Editing from revision ' + base + '.'});
  var form = h('form', {onsubmit: function (ev) {
    ev.preventDefault();
    if (mode.value === 'empty' && !confirm('Approve this context as empty? This records that it starts with no prior knowledge.')) return;
    api('POST', path + '/context', {base_revision: base, body: mode.value === 'empty' ? '' : body.value, sources: lines(sources.value),
      reason: reason.value.trim(), mode: mode.value, decision_change: decision.checked}).then(function (res) {
      if (res.ok) {
        status(out, true, mode.value === 'draft' ? 'Draft saved for review.' : 'Published.');
        reason.value = ''; decision.checked = false;
        refresh();
        return;
      }
      if (res.status === 409) {
        // someone else changed it: keep the draft, show what is current now, and let them rebase by hand
        status(out, false, 'Not saved: the context changed since revision ' + base + ' (' + why(res) + '). Your text is kept here; the current version is shown above. Merge your change into it, then save again.');
        refresh(true);
        return;
      }
      status(out, false, why(res));   // the draft stays in the form
    });
  }},
    baseNote, field('Context', body, 'purpose and scope, priorities, decisions and constraints with links, what worked and failed, open questions'),
    field('Sources', sources), field('Reason', reason),
    h('label', {'class': 'inline'}, decision, 'this changes a decision or constraint'),
    field('Save as', mode), h('button', {type: 'submit', text: 'Save'}), out);
  add(sec, h('details', null, h('summary', {text: canManage ? 'Edit context' : 'Propose a change (saved as a draft for a human to approve)'}), form));

  var hist = h('div');
  add(sec, h('details', {ontoggle: function (ev) { if (ev.target.open && !hist.firstChild) loadHistory(); }}, h('summary', {text: 'Drafts and history'}), hist));

  function refresh(keepForm) {   // fetch the current revision again; the form keeps its text when asked to
    api('GET', path).then(function (res) {
      if (!res.ok) return;
      var c = (res.data.project || res.data.group || {}).context || {revision: 0};
      drawCurrent(c);
      base = c.revision || 0;
      baseNote.textContent = 'Editing from revision ' + base + (keepForm ? ' (updated after the conflict; your text is unchanged).' : '.');
      if (!keepForm) { body.value = c.body || ''; sources.value = (c.sources || []).join('\n'); }
      clear(hist); if (hist.parentNode.open) loadHistory();
    });
  }

  function loadHistory(before) {
    var list = h('table');
    if (!before) clear(hist);
    var hout = h('div', {role: 'status', 'aria-live': 'polite'});
    add(hist, [list, hout]);
    api('GET', path + '/context/revisions' + (before ? '?before=' + encodeURIComponent(before) : '')).then(function (res) {
      if (!res.ok) { status(hout, false, 'Could not load the history: ' + why(res)); return; }
      var revs = res.data.revisions || [];
      if (!revs.length && !before) { add(hout, h('p', {'class': 'note', text: 'No revision yet.'})); return; }
      add(list, revs.map(function (r) {
        var view = h('div');
        return h('tbody', null, h('tr', null,
          h('td', null, h('button', {type: 'button', 'class': 'quiet', 'aria-expanded': 'false', onclick: function (ev) {
            var b = ev.currentTarget;
            if (view.firstChild) { clear(view); b.setAttribute('aria-expanded', 'false'); return; }
            b.setAttribute('aria-expanded', 'true');
            showRevision(r.id, view);
          }, text: '#' + r.id})),
          h('td', null, badge(r.status || '', r.status === 'draft' || r.status === 'pending' ? 'draft' : r.status === 'current' || r.status === 'published' ? 'ready' : '')),
          h('td', {'class': 'note', text: (r.author ? '@' + r.author + ' ' : '') + when(r.created_at) + (r.base_revision != null ? ', from revision ' + r.base_revision : '')}),
          h('td', {text: r.preview || ''})),
          h('tr', null, h('td', {colspan: 4}, view)));
      }));
      if (res.data.next_before) add(hout, h('button', {type: 'button', 'class': 'quiet', onclick: function (ev) { ev.currentTarget.remove(); loadHistory(res.data.next_before); }, text: 'older'}));
    });
  }

  function showRevision(id, view) {
    add(view, h('p', {'class': 'note', text: 'loading…'}));
    api('GET', path + '/context/revisions/' + encodeURIComponent(id)).then(function (res) {
      clear(view);
      if (!res.ok) { add(view, h('p', {'class': 'msg-err', text: why(res)})); return; }
      var r = res.data.revision || {}, vout = h('div', {role: 'status', 'aria-live': 'polite'});
      add(view, h('div', {'class': 'ctx', text: r.body || '(empty)'}),
        (r.sources || []).length ? h('div', {'class': 'note'}, 'Sources:', h('ul', {'class': 'plain'}, r.sources.map(srcItem))) : null,
        r.reason ? h('p', {'class': 'note', text: 'Reason: ' + r.reason}) : null,
        h('p', {'class': 'note', text: 'Based on revision ' + (r.base_revision != null ? r.base_revision : '?') + (r.base_revision !== base ? '; the current one is ' + base + ', so check what changed since before approving.' : '.')}));
      if (canManage && (r.status === 'draft' || r.status === 'pending')) {
        add(view, h('div', {'class': 'row'},
          h('button', {type: 'button', onclick: function () {
            api('POST', path + '/context/approve', {id: r.id, base_revision: base}).then(function (ares) {
              if (ares.ok) { status(vout, true, 'Approved: it is the current context now.'); refresh(); }
              else status(vout, false, ares.status === 409 ? 'Not approved: the context changed meanwhile (' + why(ares) + '). Reload the history and check the draft against the current revision.' : why(ares));
            });
          }, text: 'Approve as current'}),
          h('button', {type: 'button', 'class': 'quiet', onclick: function () {
            body.value = r.body || ''; sources.value = (r.sources || []).join('\n');
            form.parentNode.open = true; body.focus();
          }, text: 'Edit before approving'})), vout);
      }
    });
  }
  return sec;
}
function srcItem(s) {
  s = String(s || '');
  if (/^https?:\/\//.test(s)) return h('li', null, h('a', {href: s, rel: 'noopener', text: s}));
  var m = /^#?(\d+)$/.exec(s);
  if (m) return h('li', null, h('a', {href: '/t/' + m[1], text: '#' + m[1]}));
  return h('li', null, h('code', {text: s}));
}

if (PROJECT) projectView(); else listView();
})();
"""


def projects_page(name: str) -> str:
    """The whole page for /projects and /projects/{slug}; the script fills it in."""
    return shell(name, "projects",
                 '<style>' + PROJECTS_CSS + '</style>'
                 '<div class="pj" id="pj"><p class="sub">Loading projects…</p>'
                 '<noscript><p class="sub">This page needs JavaScript. The same data is at '
                 '<a href="/api/projects">/api/projects</a>.</p></noscript></div>'
                 '<script>' + PROJECTS_JS + '</script>')
