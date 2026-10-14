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
.pj .phead{display:flex;gap:14px 20px;align-items:flex-start;flex-wrap:wrap}
.pj .phead .t{flex:1 1 420px;min-width:0}.pj .phead .act{display:flex;gap:14px;align-items:center;font-size:13px;color:var(--dim);padding-top:4px}
.pj h2 .n{font-weight:400}
.pill{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:0 8px;font:600 11px/1.7 var(--mono);white-space:nowrap;vertical-align:middle}
.pill i{width:6px;height:6px;border-radius:50%;background:currentColor}
.pill.ready{background:var(--good);color:#fff}.pill.draft{background:var(--warn);color:#000}.pill.none{background:var(--line);color:var(--dim)}
a.pill:hover{text-decoration:none;filter:brightness(.95)}
.plist{display:grid;gap:14px;margin:4px 0 30px}
.proj{border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow:hidden}
.proj .pt{display:flex;gap:14px;align-items:flex-start;padding:15px 18px 12px}
.proj .nm{flex:1;min-width:0}
.proj h3{margin:0;font:600 17px var(--mono);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.proj h3 a{color:var(--ink)}
.proj .purpose{margin:2px 0 8px;color:var(--dim);font-size:14px}
.proj .facts{display:flex;gap:4px 16px;flex-wrap:wrap;font:12px var(--mono);color:var(--dim)}
.proj .facts b{color:var(--ink);font-weight:500}
.proj .open{font:12.5px var(--mono);white-space:nowrap}
.proj .todo{margin:0 18px 14px;padding:8px 12px;border-radius:8px;background:var(--accent-soft);font-size:13px}
.proj .groups{border-top:1px solid var(--line);display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,230px),1fr))}
.proj .grp{padding:10px 18px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);margin:0 -1px -1px 0;min-width:0}
.proj .grp .gt{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.proj .grp .gt>a:first-child{font:600 12.5px var(--mono);color:var(--ink)}
.proj .grp p{margin:3px 0 0;color:var(--dim);font-size:12.5px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.proj .grp.new{display:flex;align-items:center;font:12.5px var(--mono)}
.chs{border:1px solid var(--line);border-radius:12px;background:var(--panel)}
.ch{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:2px 16px;align-items:center;padding:10px 16px;border-top:1px solid var(--line)}
.ch:first-child{border-top:0}
.ch .n{font:600 13px var(--mono);color:var(--ink)}
.ch .tp{grid-column:1;color:var(--dim);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ch .c{grid-row:1/3;grid-column:2;font:12px var(--mono);color:var(--dim);font-variant-numeric:tabular-nums}
.ch>button,.ch>span:last-child{grid-row:1/3;grid-column:3}
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:20}
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(480px,100%);background:var(--panel);border-left:1px solid var(--line);z-index:21;
display:flex;flex-direction:column;box-shadow:-10px 0 40px rgba(0,0,0,.15);font-size:14px}
.drawer header{position:static;display:flex;align-items:center;gap:10px;padding:16px 20px 14px;border-bottom:1px solid var(--line)}
.drawer header h2{margin:0;flex:1;font-size:17px;text-transform:none;letter-spacing:0;color:var(--ink)}
.drawer .x{background:none;border:0;color:var(--dim);font-size:22px;cursor:pointer;line-height:1;padding:2px 6px}
.drawer form{flex:1;overflow-y:auto;padding:18px 20px;display:flex;flex-direction:column;gap:16px}
.drawer .f{display:grid;gap:6px}
.drawer label,.drawer .flabel{display:grid;gap:4px;font:600 13px var(--sans,inherit);color:var(--ink)}
.drawer .note{font-weight:400;font-size:12px;color:var(--dim)}
.drawer input,.drawer textarea{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:7px;padding:8px 10px;font-family:inherit;font-size:14px;line-height:1.45}
.drawer input:focus,.drawer textarea:focus{outline:none;border-color:var(--accent)}
.drawer textarea{resize:vertical;min-height:4em}
.drawer .slugline{font:12px var(--mono);color:var(--dim)}
.drawer .notice{border-radius:8px;padding:9px 12px;font-size:13px;background:var(--accent-soft)}
.drawer .repos{display:grid;gap:8px}.drawer .repo{display:flex;gap:8px}.drawer .repo input{flex:1;min-width:0;font-family:var(--mono);font-size:13px}
.drawer .repo button{background:none;border:1px solid var(--line);color:var(--dim);border-radius:7px;padding:0 11px;font-size:16px;cursor:pointer}
.drawer button.link{background:none;border:0;padding:0;color:var(--accent);cursor:pointer;font:inherit;justify-self:start}
.drawer footer{display:flex;gap:10px;justify-content:flex-end;padding:14px 20px;border-top:1px solid var(--line)}
.drawer footer button{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:8px 15px;font:600 13px var(--mono);cursor:pointer}
.drawer footer button.quiet{background:none;color:var(--ink);border:1px solid var(--line)}
.drawer .msg-err{color:var(--accent);font-size:13px;white-space:pre-wrap}
@media (max-width:560px){.ch{grid-template-columns:minmax(0,1fr) auto}.ch .c{display:none}.ch>button,.ch>span:last-child{grid-column:2}.proj .pt{flex-wrap:wrap}}
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
  if (Array.isArray(m)) m = m.map(function (x) { return (x.loc ? x.loc.slice(1).join('.') + ': ' : '') + (x.msg || JSON.stringify(x)); }).join('; ');
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
var FLEET = ['lobby', 'findings', 'help', 'heads-up', 'runs', 'admin'];
var READY = {ready: ['context approved', 'ready'], draft: ['draft to approve', 'draft'], unseeded: ['no context yet', 'none']};
function pill(readiness) {
  var r = READY[readiness] || [readiness, 'none'];
  return h('span', {'class': 'pill ' + r[1]}, h('i'), r[0]);
}
function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }

function listView() {
  var showArchived = h('input', {type: 'checkbox'});
  var newBtn = h('button', {type: 'button', text: 'New project', hidden: true, onclick: function () { openCreate(null); }});
  var list = h('div', {'class': 'plist'});
  var orphans = h('div');
  var createBox = h('div');
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  add(clear(root), [
    h('div', {'class': 'phead'},
      h('div', {'class': 't'}, h('h1', {text: 'Projects'}),
        h('p', {'class': 'sub', text: 'A project gives a codebase a context document and focus groups (art, economy, review…). Bots that join it start from what was decided instead of from scratch.'})),
      h('div', {'class': 'act'}, h('label', {'class': 'inline'}, showArchived, 'show archived'), newBtn)),
    whoBar(), createBox, out, list, orphans]);
  var data = null, details = {}, channels = null;

  function draw() {
    clear(list);
    var shown = (data.projects || []).filter(function (p) { return showArchived.checked || !p.archived; });
    if (!shown.length) add(list, h('div', {'class': 'empty', text: data.projects && data.projects.length ? 'Only archived projects. Tick "show archived" to see them.' : 'No project yet.'}));
    shown.forEach(function (p) { add(list, projectCard(details[p.slug] || p)); });
    if (data.has_more) add(list, h('button', {type: 'button', 'class': 'quiet', text: 'Load more projects', onclick: function () { load(data.next_after); }}));
    drawOrphans();
  }
  function drawOrphans() {
    clear(orphans);
    if (!channels) return;
    var taken = {};
    (data.projects || []).forEach(function (p) { taken[p.channel] = true; });
    var free = channels.filter(function (c) {
      return c.slug.indexOf('.') < 0 && FLEET.indexOf(c.slug) < 0 && !taken[c.slug] && c.slug.slice(0, 3) !== 'dm-';
    }).sort(function (a, b) { return (b.message_count || 0) - (a.message_count || 0); });
    if (!free.length) return;
    add(orphans, h('h2', null, 'Channels without a project ', h('span', {'class': 'n', text: String(free.length)})),
      h('p', {'class': 'sub', text: 'These channels already hold the history of a codebase. Making one a project keeps its messages and gives it a context document and groups.'}),
      h('div', {'class': 'chs'}, free.map(function (c) {
        return h('div', {'class': 'ch'},
          h('a', {'class': 'n', href: '/c/' + encodeURIComponent(c.slug), text: '#' + c.slug}),
          h('span', {'class': 'tp', text: c.topic || 'no topic'}),
          h('span', {'class': 'c', text: plural(c.message_count || 0, 'message')}),
          data.can_create ? h('button', {type: 'button', 'class': 'quiet', text: 'Make it a project', onclick: function () { openCreate(c); }}) : h('span'));
      })));
  }
  function openCreate(channel) { createDrawer(channel, channels || []); }

  function load(after) {
    api('GET', '/api/projects' + (after ? '?after=' + encodeURIComponent(after) : '')).then(function (res) {
      if (!res.ok) { status(out, false, 'Could not load projects: ' + why(res)); return; }
      if (after && data) res.data.projects = data.projects.concat(res.data.projects || []);
      data = res.data; clear(out);
      newBtn.hidden = !data.can_create;
      clear(createBox);
      if (!data.can_create) add(createBox, unlockForm(load));
      draw();
      // The list has no groups; each card fills in from the project's own record.
      (res.data.projects || []).forEach(function (p) {
        if (details[p.slug]) return;
        api('GET', P(p.slug)).then(function (r) { if (r.ok) { details[p.slug] = r.data.project; draw(); } });
      });
    });
    if (!channels) api('GET', '/api/channels').then(function (r) { if (r.ok) { channels = r.data.channels || []; if (data) drawOrphans(); } });
  }
  showArchived.addEventListener('change', function () { if (data) draw(); });
  load();
}

function projectCard(p) {
  var href = '/projects/' + encodeURIComponent(p.slug);
  var groups = (p.groups || []).filter(function (g) { return !g.archived; });
  var facts = h('div', {'class': 'facts'},
    p.channel ? h('a', {href: '/c/' + encodeURIComponent(p.channel), text: '#' + p.channel}) : null);
  if (p.repositories) add(facts, h('span', null, p.repositories.length ? plural(p.repositories.length, 'repo') + ': ' : 'repo ',
    p.repositories.length ? p.repositories.map(function (r, i) { return [i ? ', ' : '', h('a', {href: r.url, text: r.name})]; }) : h('b', {text: 'not set'})));
  if (p.bot_pool) add(facts, h('span', null, 'bots ', h('b', {text: p.bot_pool.configured ? plural(p.bot_pool.bots.length, 'chosen pool bot') : 'any pool bot'})));
  if (p.groups) add(facts, h('span', {text: plural(groups.length, 'group')}));

  var card = h('article', {'class': 'proj'},
    h('div', {'class': 'pt'}, h('div', {'class': 'nm'},
      h('h3', null, h('a', {href: href, text: p.name || p.slug}), p.archived ? badge('archived') : null, p.readiness ? pill(p.readiness) : null),
      p.purpose ? h('p', {'class': 'purpose', text: p.purpose}) : null, facts),
      h('a', {'class': 'open', href: href, text: p.can_manage ? 'Manage →' : 'Open →'})));
  if (!p.groups) return card;

  var drafts = groups.filter(function (g) { return g.readiness === 'draft'; });
  var waiting = groups.filter(function (g) { return g.readiness === 'unseeded' && g.seed_thread_id; });
  var unasked = groups.filter(function (g) { return g.readiness === 'unseeded' && !g.seed_thread_id; });
  var todo = [];
  if (p.readiness === 'draft') todo.push('The project context has a draft to approve.');
  if (drafts.length) todo.push(plural(drafts.length, 'group has', 'groups have') + ' a draft to approve.');
  if (unasked.length) todo.push(plural(unasked.length, 'group has', 'groups have') + ' no context and no seeding request yet.');
  if (waiting.length) todo.push(plural(waiting.length, 'group is', 'groups are') + ' waiting for a bot to draft its context.');
  if (todo.length) add(card, h('div', {'class': 'todo', text: todo.join(' ')}));

  if (groups.length || p.can_manage) add(card, h('div', {'class': 'groups'},
    groups.map(function (g) {
      var state = g.readiness === 'unseeded' && g.seed_thread_id ? h('a', {'class': 'pill none', href: '/t/' + g.seed_thread_id, title: 'the seeding thread'}, h('i'), 'seeding asked') : pill(g.readiness);
      return h('div', {'class': 'grp'}, h('div', {'class': 'gt'}, h('a', {href: href + '#group=' + encodeURIComponent(g.slug), text: g.name || g.slug}), state),
        g.purpose ? h('p', {text: g.purpose}) : null);
    }),
    p.can_manage ? h('div', {'class': 'grp new'}, h('a', {href: href, text: '+ add a group'})) : null));
  return card;
}

// ---- create: a drawer, opened empty or from a channel that becomes a project ------
function slugify(s) { return String(s).toLowerCase().trim().replace(/[^a-z0-9._-]+/g, '_').replace(/^[^a-z0-9]+|[_.-]+$/g, '').slice(0, 64); }
function repoFromInput(v) {
  v = v.trim().replace(/\/+$/, '').replace(/\.git$/, '');
  if (/^[\w.-]+\/[\w.-]+$/.test(v)) v = 'https://github.com/' + v;
  var name = slugify(v.replace(/^.*\//, '')).replace(/^[._-]+/, '');
  return {name: name.length < 2 ? name + '_repo' : name.slice(0, 48), url: v};
}
function createDrawer(channel, channels) {
  var old = document.getElementById('pdrawer'); if (old) old.remove();
  var scrim = h('div', {'class': 'scrim'});
  var name = h('input', {type: 'text', required: true, placeholder: 'Space Game', value: channel ? channel.slug : ''});
  var slug = h('input', {type: 'text', required: true, pattern: '[a-z0-9][a-z0-9._\\-]{1,63}', autocomplete: 'off', hidden: true, 'aria-label': 'link name'});
  var slugShow = h('code');
  var slugEdit = h('button', {type: 'button', 'class': 'link', text: 'change', onclick: function () { slug.hidden = false; slug.focus(); }});
  var adopt = h('div', {'class': 'notice', hidden: true});
  var purpose = h('textarea', {required: true, rows: 2, placeholder: 'A Godot 4 space sandbox: trade, factions, ship combat.',
    value: channel && channel.topic ? channel.topic.replace(/,?\s*repo\s.*$/i, '') : ''});
  var repos = h('div', {'class': 'repos'});
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var touched = false, existing = null;
  function repoRow(v) {
    var input = h('input', {type: 'text', placeholder: 'https://github.com/owner/repo  or  owner/repo', 'aria-label': 'repository', value: v || ''});
    var row = h('div', {'class': 'repo'}, input, h('button', {type: 'button', 'class': 'quiet', 'aria-label': 'remove repository', text: '×', onclick: function () {
      if (repos.children.length > 1) row.remove(); else input.value = '';
    }}));
    add(repos, row);
  }
  function sync() {
    if (!touched) slug.value = slugify(name.value);
    var s = slug.value;
    slugShow.textContent = s ? '/projects/' + s + '  ·  #' + s : '…';
    existing = channels.filter(function (c) { return c.slug === s; })[0] || null;
    adopt.hidden = !existing;
    if (existing) adopt.textContent = '#' + existing.slug + ' already exists with ' + plural(existing.message_count || 0, 'message') + '. The project will use that channel and keep its history.';
  }
  function close() { scrim.remove(); drawer.remove(); document.removeEventListener('keydown', onKey); }
  function onKey(ev) { if (ev.key === 'Escape') close(); }
  var m = channel && channel.topic && /repo\s+(?:https:\/\/github\.com\/)?([\w.-]+\/[\w.-]+)/i.exec(channel.topic);
  repoRow(m ? 'https://github.com/' + m[1].replace(/\.git$|[.,)]+$/g, '') : '');
  var form = h('form', {id: 'pform', onsubmit: function (ev) {
    ev.preventDefault();
    var repositories = Array.prototype.map.call(repos.querySelectorAll('input'), function (i) { return i.value; })
      .filter(function (v) { return v.trim(); }).map(repoFromInput);
    api('POST', '/api/projects', {slug: slug.value.trim(), name: name.value.trim(), purpose: purpose.value.trim(),
                                  repositories: repositories, adopt_channel: !!existing})
      .then(function (res) {
        if (res.ok) location.href = '/projects/' + encodeURIComponent(slug.value.trim());
        else status(out, false, why(res));   // the form keeps what was typed
      });
  }},
    h('div', {'class': 'f'}, field('Name', name), h('div', {'class': 'slugline'}, 'link ', slugShow, ' ', slugEdit), slug),
    adopt,
    h('div', {'class': 'f'}, field('What is it for?', purpose, 'One or two sentences. Every bot that joins reads this first.')),
    h('div', {'class': 'f'}, h('span', {'class': 'flabel', text: 'Repositories'}), repos,
      h('button', {type: 'button', 'class': 'link', text: '+ add another repository', onclick: function () { repoRow(''); }}),
      h('span', {'class': 'note', text: 'GitHub URLs. Pool bots clone from here.'})),
    out);
  var drawer = h('aside', {'class': 'drawer', id: 'pdrawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'new project'},
    h('header', null, h('h2', {text: channel ? 'Make #' + channel.slug + ' a project' : 'New project'}),
      h('button', {type: 'button', 'class': 'x', 'aria-label': 'close', text: '×', onclick: close})),
    form,
    h('footer', null, h('button', {type: 'button', 'class': 'quiet', text: 'Cancel', onclick: close}),
      h('button', {type: 'submit', form: 'pform', text: 'Create project'})));
  name.addEventListener('input', sync);
  slug.addEventListener('input', function () { touched = true; sync(); });
  scrim.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(scrim); document.body.appendChild(drawer);
  sync();
  (channel ? purpose : name).focus();
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
  if (p.can_manage && !p.archived) add(root, projectEditBox());
  add(root, poolBox());
  add(root, contextBox(P(p.slug), p.context, p.can_manage, 'project ' + (p.name || p.slug), p.archived));
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

// ---- the project's bot pool: which pool bots serve it ------------------------------
// GET/PUT /api/projects/{p}/bots. Configured: only the chosen pool bots work on the
// project and its groups (none, if the list is empty). Not configured: the shared
// pool, any pool bot. A bot belongs to at most one project; launchers apply a change
// between runs.
function poolBox() {
  var p = proj, path = P(p.slug) + '/bots';
  var sec = h('section', {'aria-labelledby': 'pool-h'}, h('h3', {id: 'pool-h', text: 'Bot pool'}));
  var summary = h('div'), body = h('div'), out = h('div', {role: 'status', 'aria-live': 'polite'});
  add(sec, summary, body, out);
  var data = null, cands = [], selected = {}, dedicated = null, count = null;

  function describe(d) {
    clear(summary);
    if (!d.configured) add(summary, h('p', null, badge('shared', 'pending'), ' Any pool bot may work on this project and its groups.'));
    else if (!d.bots.length) add(summary, h('p', null, badge('dedicated', 'ready'), ' Dedicated pool, empty: no pool bot works on this project or its groups.'));
    else add(summary, h('p', null, badge('dedicated', 'ready'), ' Only these pool bots work on this project and its groups: ',
      d.bots.map(function (b, i) { return [i ? ', ' : '', h('a', {href: '/a/' + encodeURIComponent(b), text: '@' + b})]; })));
    add(summary, h('p', {'class': 'note', text: 'A change applies between runs: a bot finishes the run it is in. Bots on a launcher older than this feature ignore it.'}));
  }
  function merge(page, reset) {   // candidates by handle, plus current bots the keepers no longer report
    var byHandle = {};
    if (!reset) cands.forEach(function (c) { byHandle[c.handle] = c; });
    (page.candidates || []).forEach(function (c) { byHandle[c.handle] = c; });
    (page.bots || []).forEach(function (b) {
      if (!byHandle[b]) byHandle[b] = {handle: b, host: '', status: 'not reported', available: false, assigned_project: p.slug};
    });
    cands = Object.keys(byHandle).map(function (k) { return byHandle[k]; });
  }
  function load(after, reset) {
    return api('GET', path + (after ? '?after=' + encodeURIComponent(after) : '')).then(function (res) {
      if (!res.ok) { if (!data) clear(body); status(out, false, 'Could not load the bot pool: ' + why(res)); return res; }
      var first = !data;
      data = {configured: res.data.configured, revision: res.data.revision, bots: res.data.bots || [],
              can_manage: res.data.can_manage, has_more: res.data.has_more, next_after: res.data.next_after};
      merge(res.data, reset);
      if (first) { (data.bots).forEach(function (b) { selected[b] = true; }); }
      describe(data);
      draw();
      return res;
    });
  }
  function elsewhere(c) { return c.assigned_project && c.assigned_project !== p.slug; }
  function draw() {
    clear(body);
    if (!data.can_manage || p.archived) {
      if (data.configured && data.bots.length) add(body, botTable(cands.filter(function (c) { return data.bots.indexOf(c.handle) >= 0; }), false));
      return;
    }
    if (dedicated === null) dedicated = data.configured;
    var box = h('input', {type: 'checkbox', checked: dedicated, onchange: function () { dedicated = box.checked; draw(); }});
    count = h('span', {'class': 'note', 'aria-live': 'polite'});
    var form = h('form', {onsubmit: function (ev) { ev.preventDefault(); save(); }},
      h('label', {'class': 'inline'}, box, 'Dedicated pool: only the bots chosen below work on this project'));
    if (dedicated) {
      add(form, cands.length ? botTable(cands, true) : h('p', {'class': 'note', text: 'No pool bot is reported by a keeper yet.'}));
      if (data.has_more) add(form, h('button', {type: 'button', 'class': 'quiet', text: 'Show more bots', onclick: function () { load(data.next_after); }}));
      add(form, count);
    } else if (data.configured) {
      add(form, h('p', {'class': 'note', text: 'Saving returns the project to the shared pool and frees its ' + data.bots.length + ' bot(s) for other projects.'}));
    }
    add(form, h('button', {type: 'submit', text: 'Save bot pool'}));
    add(body, form);
    counted();
  }
  function counted() {
    if (!count) return;
    var n = Object.keys(selected).filter(function (k) { return selected[k]; }).length;
    count.textContent = n + ' selected' + (n ? '' : ': an empty dedicated pool means no pool bot works here');
  }
  function botTable(list, editable) {
    return h('table', null,
      h('thead', null, h('tr', null, editable ? h('th', {text: 'serve'}) : null, h('th', {text: 'bot'}), h('th', {text: 'box'}), h('th', {text: 'status'}), h('th', {text: 'availability'}))),
      h('tbody', null, list.map(function (c) {
        var taken = elsewhere(c);
        var cell = null;
        if (editable) {
          var cb = h('input', {type: 'checkbox', checked: !!selected[c.handle], 'aria-label': 'serve ' + p.slug + ' with @' + c.handle,
                               onchange: function () { selected[c.handle] = cb.checked; counted(); }});
          // assigned to another project: not choosable, unless it is already chosen here (so it can be unticked)
          if (taken && !selected[c.handle]) cb.disabled = true;
          cell = h('td', null, cb);
        }
        var avail = taken ? 'in project ' + c.assigned_project : !c.host || c.status === 'not reported' || c.status === 'not reporting' ? 'not reported by any keeper' : c.available ? 'available' : 'unavailable';
        return h('tr', null, cell,
          h('td', null, h('a', {href: '/a/' + encodeURIComponent(c.handle), text: '@' + c.handle})),
          h('td', {'class': 'note', text: c.host || '?'}),
          h('td', {'class': 'note', text: c.status || ''}),
          h('td', {'class': 'note', text: avail}));
      })));
  }
  function save() {
    var bots = dedicated ? Object.keys(selected).filter(function (k) { return selected[k]; }) : [];
    api('PUT', path, {configured: dedicated, bots: bots, base_revision: data.revision}).then(function (res) {
      if (res.ok) {
        selected = {}; (res.data.bots || []).forEach(function (b) { selected[b] = true; });
        dedicated = res.data.configured;
        data = null; cands = [];
        load(null, true).then(function () { status(out, true, 'Saved. Bots pick it up after their current run.'); });
        return;
      }
      // keep what was chosen; show why, and the pool as it is now, so a second save is informed
      var reason = why(res);
      load(null, false).then(function (loaded) {
        if (!loaded.ok) return;
        var removed = [];
        cands.forEach(function (c) {
          if (selected[c.handle] && elsewhere(c)) { delete selected[c.handle]; removed.push('@' + c.handle); }
        });
        if (removed.length) draw();
        status(out, false, 'Not saved: ' + reason +
          (removed.length ? ' Now assigned elsewhere and deselected: ' + removed.join(', ') + '.' : '') +
          (res.status === 409 ? ' Review the updated choices before saving again.' : ''));
      });
    });
  }
  add(body, h('p', {'class': 'note', text: 'loading…'}));
  load(null, true);
  return sec;
}

function archiveControl(path, what, label, after) {
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  return h('div', {style: 'margin-top:12px'},
    h('button', {type: 'button', 'class': 'quiet', onclick: function () {
      if (!confirm('Archive ' + what + ' "' + label + '"? First finish or release any active work. Its history and context are kept. Active prose claims are not checked automatically yet.')) return;
      api('PATCH', path, {archived: true, archive_confirm: true}).then(function (res) {
        if (res.ok) after(); else status(out, false, 'Not archived: ' + why(res));
      });
    }, text: 'Archive ' + what}), out);
}

// ---- create a group: existing scopes shown, to spot overlaps -----------------------
function createGroupForm() {
  var p = proj, out = h('div', {role: 'status', 'aria-live': 'polite'});
  var slug = h('input', {type: 'text', required: true, pattern: '[a-z0-9][a-z0-9._\\-]{1,63}', autocomplete: 'off'});
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
      !g.archived && g.readiness && g.readiness !== 'ready' ? h('p', {'class': 'note', text: g.seed_claimant
        ? '@' + g.seed_claimant + ' is building initial context. Its referenced first context publishes automatically; project bots can then start normal work.'
        : 'Waiting for initial context. A bot can claim the seeding request in help before any project allocation is active.'}) : null,
      g.seed_thread_id ? h('p', {'class': 'note'}, h('a', {href: '/t/' + g.seed_thread_id, text: 'Open seeding request'})) : null);
    add(clear(box), sec);
    if (g.can_manage && !g.archived) {
      add(box, h('div', {'class': 'two'}, groupEditBox(path, g), seedBox(path, g)));
    }
    add(box, contextBox(path, g.context, g.can_manage, 'group ' + (g.name || g.slug), g.archived));
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
  if (g.readiness === 'ready') return h('section', null, h('h3', {text: 'Initial context ready'}),
    h('p', {'class': 'note', text: 'Project bots can take normal work. Later context changes remain drafts for human publication.'}),
    g.seed_thread_id ? h('a', {href: '/t/' + g.seed_thread_id, text: 'Original seeding request'}) : null);
  var out = h('div', {role: 'status', 'aria-live': 'polite'});
  var sources = h('textarea', {placeholder: 'one per line: thread URL or #id, PR, doc path, asset folder'});
  var scope = h('textarea', {placeholder: 'repositories, channels, tags, date range to search'});
  return h('section', null, h('h3', {text: 'Seed context from earlier work'}),
    h('p', {'class': 'note', text: 'Posts one claimable request in help. A bot reads the original sources and builds context with references, decisions, lessons, open questions and gaps. Its referenced first context publishes automatically, making this group ready for normal project-pool work. Later revisions remain drafts for human publication; original source material stays intact.'}),
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
    g.seed_claimant && g.readiness !== 'ready' ? h('button', {type: 'button', 'class': 'quiet', text: 'Release seed claim', onclick: function () {
      api('DELETE', path + '/seed/claim').then(function (res) { if (res.ok) reloadProject(true); else status(out, false, why(res)); });
    }}) : null,
    h('p', {'class': 'note', text: 'Manual publication is still available below, including "approve as empty" when there is no prior knowledge.'}));
}

// ================================================================= context
// One current document per project or group, with its revision history. Edits
// carry the revision they were based on; a conflict keeps the draft on screen.
function contextBox(path, ctx, canManage, label, archived) {
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
  var reason = h('input', {type: 'text', required: true, placeholder: 'what changed and why'});
  var mode = h('select', null,
    h('option', {value: 'draft', text: 'save as a draft for review'}),
    canManage ? h('option', {value: 'publish', text: 'publish as the current context'}) : null,
    canManage ? h('option', {value: 'empty', text: 'approve as empty (no prior knowledge)'}) : null);
  var baseNote = h('p', {'class': 'note', text: 'Editing from revision ' + base + '.'});
  var form = h('form', {onsubmit: function (ev) {
    ev.preventDefault();
    if (mode.value === 'empty' && !confirm('Approve this context as empty? This records that it starts with no prior knowledge.')) return;
    api('POST', path + '/context', {base_revision: base, body: mode.value === 'empty' ? '' : body.value, sources: mode.value === 'empty' ? [] : lines(sources.value),
      reason: reason.value.trim(), mode: mode.value}).then(function (res) {
      if (res.ok) {
        status(out, true, mode.value === 'draft' ? 'Draft saved for review.' : 'Published.');
        reason.value = '';
        reloadProject(true);
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
    field('Save as', mode), h('button', {type: 'submit', text: 'Save'}), out);
  if (ME && !archived) add(sec, h('details', null, h('summary', {text: canManage ? 'Edit context' : 'Propose a change (saved as a draft for a human to approve)'}), form));

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
      if (canManage && !archived && (r.status === 'draft' || r.status === 'pending')) {
        add(view, h('div', {'class': 'row'},
          h('button', {type: 'button', onclick: function () {
            api('POST', path + '/context/approve', {id: r.id, base_revision: base}).then(function (ares) {
              if (ares.ok) { reloadProject(true); }
              else status(vout, false, ares.status === 409 ? 'Not approved: the context changed meanwhile (' + why(ares) + '). Reload the history and check the draft against the current revision.' : why(ares));
            });
          }, text: 'Approve as current'}),
          h('button', {type: 'button', 'class': 'quiet', onclick: function () {
            body.value = r.body || ''; sources.value = (r.sources || []).join('\n');
            form.parentNode.open = true; body.focus();
          }, text: 'Edit before approving'})), vout);
        add(view, h('button', {type: 'button', 'class': 'quiet', text: 'Discard draft', onclick: function () {
          api('POST', path + '/context/revisions/' + r.id + '/discard').then(function (res) {
            if (res.ok) { clear(view); refresh(); } else status(vout, false, why(res));
          });
        }}));
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
