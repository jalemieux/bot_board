"""The workspaces page: workspaces, their channels, their bots and their knowledge base.

One page serves /workspaces (the list) and /workspaces/{slug} (one workspace);
the script reads the path and talks to /api/workspaces. Writes use the identity
the board page keeps in this browser (localStorage bb.me {handle, token}); the
server decides what that identity may do and says so in can_create /
can_manage. Data from the API is only ever put on the page as text, never as
HTML, with one exception: the workspace switcher's rows (WS_SWITCHER_JS, shared with
the board page, #660) are built as HTML with every value escaped.
"""
from __future__ import annotations

from .web import shell, sheet_css, WS_SWITCHER_JS

WS_CSS = """
.ws .l2{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;margin:0 0 14px;padding:0 0 10px;border-bottom:1px solid var(--line);font:12.5px var(--mono)}
.ws .l2 .lab{color:var(--dim)}
.ws .wsw{position:relative}
.ws .wsw>button{font:600 12.5px var(--mono);color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;min-height:32px}
.ws .wsw .badge{margin:0 4px;background:var(--accent);color:#fff}
.ws #wsmenu{position:absolute;top:36px;left:0;z-index:20;min-width:220px;margin:0;padding:4px;list-style:none;background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 24px #0004}
.ws #wsmenu[hidden]{display:none}
.ws #wsmenu button{display:flex;align-items:center;gap:8px;width:100%;text-align:left;font:12.5px var(--mono);color:var(--ink);background:none;border:0;border-radius:6px;padding:8px 10px;cursor:pointer}
.ws #wsmenu button:hover,.ws #wsmenu a:hover{background:var(--sel)}
.ws #wsmenu a{display:block;font:12.5px var(--mono);color:var(--dim);border-radius:6px;padding:8px 10px;text-decoration:none}
.ws #wsmenu li.bw{display:none;border-top:1px solid var(--line);margin-top:4px;padding-top:4px}
.ws #wsmenu .here-note,.ws #wsmenu .n{margin-left:auto;color:var(--dim);font-size:11px}
.ws #wsmenu .n.ask{color:#fff;background:var(--accent);border-radius:9px;padding:0 6px}
.ws .actstrip{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:12px 0}
.ws .actstrip .stat{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:10px 12px;background:var(--panel);border:1px solid var(--line);border-radius:8px;color:var(--ink);font:inherit;text-align:left}
.ws .actstrip button.stat{cursor:pointer;min-height:44px}.ws .actstrip button.stat[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft)}
.ws .actstrip .stat b{font:600 20px var(--mono)}.ws .actstrip .stat span{color:var(--dim);font-size:12px}
.ws .actfeed{list-style:none;margin:0 0 12px;padding:0}.ws .actfeed .ev{display:grid;grid-template-columns:72px 20px 1fr;gap:8px;padding:8px 0;border-top:1px solid var(--line)}
.ws .actfeed .day{padding:10px 0 4px;font:600 12px var(--mono);color:var(--dim);border-top:1px solid var(--line)}.ws .actfeed .day+.ev{border-top:0}
.ws .actfeed time{color:var(--dim);font:12px var(--mono)}.ws .actfeed .ic{text-align:center;color:var(--accent)}
.ws .activity .warn{padding:10px 12px;margin:8px 0;border-left:4px solid var(--accent);background:var(--accent-soft)}
.ws .activity .warn .quiet,.ws .activity .msg-err .quiet{min-height:32px}@media (max-width:700px){.ws .activity .quiet,.ws .activity .warn .quiet,.ws .activity .msg-err .quiet{min-height:44px}}
.ws .activity .empty{padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:8px}
.ws .tabs{display:flex;gap:4px 18px}
.ws .tabs a{padding:6px 2px;color:var(--ink);text-decoration:none;border-bottom:2px solid transparent}
.ws .tabs a[aria-current=page]{border-color:var(--accent);font-weight:600}
.ws .wsw>button:focus-visible,.ws #wsmenu button:focus-visible,.ws .tabs a:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.ws .agr.short{max-height:110px;overflow:hidden;-webkit-mask-image:linear-gradient(#000 60%,transparent);mask-image:linear-gradient(#000 60%,transparent)}
.ws .agr h4{margin:10px 0 2px;font:600 13px var(--mono)}
.ws .agr-text{white-space:pre-wrap;font-size:13.5px}
.ws .proposal{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}
.ws .proposal h3{margin:0 0 6px;font-size:14px}
.ws pre.diff{max-height:320px;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px;margin:6px 0;font:12px/1.5 var(--mono);white-space:pre-wrap}
.ws pre.diff .add{background:color-mix(in srgb,var(--good) 20%,transparent)}.ws pre.diff .del{background:color-mix(in srgb,var(--accent) 20%,transparent)}.ws pre.diff .gap{color:var(--dim)}
.ws .changebox textarea{width:100%;box-sizing:border-box;min-height:64px;font:13px var(--mono);background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:8px}
@media (max-width:700px){.ws .changebox textarea{font-size:16px}}
.ws .repo .linkrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.ws .repo .linkrow input{flex:1 1 220px;width:auto;min-width:0}
@media (max-width:700px){.ws .repo input[type=text]{font-size:16px;min-height:44px}.ws .repo button{min-height:44px}.ws .repo summary{min-height:44px;display:flex;align-items:center}}
.ws .changebox .warn,.ws .proposal .warn{color:var(--accent);font-weight:600}
.ws .rolehead{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;margin:14px 0 4px}
.ws .rolehead .prof{color:var(--dim);font:12px var(--mono)}
.ws .rolehead .status{font:600 12px var(--mono)}.ws .status.good{color:var(--good)}.ws .status.bad{color:var(--accent)}
.ws table.bots{width:100%;border-collapse:collapse;font-size:13px}
.ws table.bots td{padding:6px 8px;border-top:1px solid var(--line);vertical-align:baseline}
.ws .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:var(--dim)}
.ws .dot.ok{background:var(--good)}.ws .dot.warn{background:var(--warn)}.ws .dot.bad{background:var(--accent)}
/* dialogs (#645) */
.ovl{position:fixed;inset:0;background:#0007;display:flex;align-items:center;justify-content:center;z-index:50;padding:12px}
.dlg{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:18px 20px;max-width:520px;width:100%;max-height:90vh;overflow:auto}
.dlg h3{margin:0 0 10px;font-size:16px}
.dlg .form{display:flex;flex-wrap:wrap;gap:8px 16px}.dlg .form label{display:flex;gap:6px;align-items:center}
.dlg .warn{color:var(--accent);font-weight:600}
.dlg-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.ws .sr-only,.dlg .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.ws .roles h2 button{margin-left:auto;font:12px var(--mono)}
.ws .roles h2 button+button{margin-left:8px}
.ws table.bots tr.drawer td{padding:10px 12px;background:var(--bg);flex:1 1 100%}
.ws .d-facts{display:flex;flex-wrap:wrap;gap:4px 16px;font:12px var(--mono);color:var(--dim)}.ws .d-facts b{color:var(--ink);font-weight:600}
.ws .d-host{margin-top:12px}.ws .d-host h4{margin:0 0 6px;font:600 12px var(--mono);color:var(--dim)}.ws .d-apps{margin:4px 0 0;padding-left:18px;font-size:13px}
.ws .d-acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.ws .d-threads{margin:4px 0 0;padding-left:18px;font-size:13px}
.ws table.bots tr[hidden]{display:none}
.ws .drawer p.clamp{max-height:5.5em;overflow:auto;margin:4px 0}
.ws button.switch[aria-checked=true]::after{content:": on"}.ws button.switch[aria-checked=false]::after{content:": off"}
.ws .acts{white-space:nowrap}.ws .acts button,.ws .acts a{margin-right:8px}
.ws .kb-back{display:none;background:none;color:var(--accent);padding:0 4px;font:600 13px var(--mono)}      /* shown only on a phone, below */
@media (max-width:700px){
.ws table.bots,.ws table.bots tbody{display:block}
.ws table.bots tr{display:flex;flex-wrap:wrap;gap:2px 12px;padding:8px 0;border-top:1px solid var(--line)}
.ws table.bots td{border:0;padding:0}
.ws table.bots td:nth-child(2){flex:1 1 40%;order:1}.ws table.bots td:nth-child(n+3):nth-child(-n+6){order:2;font-size:12px;color:var(--dim);align-self:center}
.ws table.bots td:nth-child(7){order:3;margin-left:auto}
.ws .acts a{display:inline-flex;align-items:center;min-height:44px;min-width:44px;justify-content:center}
.ws .acts button,.dlg-actions button,.ws .roles h2 button,.ws .rolehead button,.ws .d-acts button{min-height:44px;min-width:44px;font-size:16px}
.ws .dlg textarea,.ws .dlg input[type=text]{font-size:16px}.ws .dlg label.check{min-height:44px;align-items:center}.ws .dlg input[type=checkbox]{width:22px;height:22px}
.ws .dlg select{min-height:44px;font-size:16px}
.ws .wsw>button{min-height:44px;font-size:16px}.ws .tabs a{padding:12px 2px}
""" + sheet_css(".ws #wsmenu:not([hidden])") + """.ws #wsmenu button,.ws #wsmenu a{min-height:48px;font-size:16px}.ws #wsmenu a{display:flex;align-items:center}.ws #wsmenu li.bw{display:block}
.ws pre.diff{max-height:50vh}
.ws .kb-side{max-width:none;flex-basis:100%}.ws .kb-reader{flex-basis:100%}
.ws .kb.reading .kb-side{display:none}.ws .kb:not(.reading) .kb-reader{display:none}.ws .kb-back{display:inline-flex;align-items:center;min-height:44px}}
.ws .kb:not(.reading) .kb-back{display:none}
.ws .sub{color:var(--dim);font-size:13px}
.ws section{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin:0 0 14px}
.ws section h2{margin:0 0 8px;font-size:15px;text-transform:none;letter-spacing:0;display:flex;gap:10px;align-items:baseline}
.ws section h2 small{font:12px var(--mono);color:var(--dim)}
.ws form{display:grid;gap:8px;margin:6px 0 0}
.ws label{display:grid;gap:3px;font-size:12px;color:var(--dim);font-family:var(--mono)}
.ws label.check{display:flex;gap:8px;align-items:flex-start;font-family:inherit;font-size:13px}
.ws label.check input{width:auto;margin-top:2px}
.ws input[type=text],.ws input[type=password],.ws textarea,.ws select{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:6px 8px;font:13px/1.45 var(--mono);width:100%}
.ws textarea{min-height:5em;resize:vertical}
.ws button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 13px;font:600 12px var(--mono);cursor:pointer;justify-self:start}
.ws button.quiet{background:var(--accent-soft);color:var(--accent)}
.ws button.danger{background:transparent;color:#b42318;border:1px solid currentColor}
.ws button.link{background:none;color:var(--accent);padding:0;font-weight:400;text-decoration:underline}
.ws button:disabled{opacity:.5;cursor:default}
.ws .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.ws .msg-ok{color:var(--good);font-size:13px}.ws .msg-err{color:var(--accent);font-size:13px;white-space:pre-wrap}
.ws details summary{cursor:pointer;color:var(--dim);font-size:13px}
.ws .cards{display:grid;gap:14px;margin:4px 0 20px}
.ws .card{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:14px 18px}
.ws .card h3{margin:0;font:600 16px var(--mono)}
.ws .card h3 a{color:var(--ink)}
.ws .card p{margin:4px 0 8px;color:var(--dim);font-size:13.5px}
.ws .facts{display:flex;gap:4px 16px;flex-wrap:wrap;font:12px var(--mono);color:var(--dim)}
.ws .facts b{color:var(--ink);font-weight:500}
.ws .badge{display:inline-block;border-radius:4px;padding:0 6px;font:11px var(--mono);background:var(--line);color:var(--dim);margin-left:6px;vertical-align:middle}
.ws .badge.archived{background:var(--dim);color:var(--bg)}
.ws .warn{color:var(--warn)}
.ws .chs{display:grid;gap:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.ws .ch{padding:10px 14px;border-top:1px solid var(--line);display:grid;gap:3px}
.ws .ch:first-child{border-top:0}
.ws .ch .n{font:600 13px var(--mono)}.ws .ch .n a{color:var(--ink)}
.ws .ch .tp,.ws .ch .fc{color:var(--dim);font-size:12.5px;white-space:pre-wrap}
.ws .ch .fc{border-left:3px solid var(--line);padding-left:10px}
.ws .ch .refs{font:12px var(--mono);color:var(--dim)}
.ws .kb{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start;margin-top:8px}
.ws .kb-side{flex:1 1 220px;max-width:300px;display:grid;gap:8px;min-width:0}
.ws .kb-side input{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:6px 8px;font:12.5px var(--mono);width:100%;box-sizing:border-box}
.ws .kb-tree{display:grid;gap:10px;max-height:70vh;overflow:auto}
.ws .kb-fname{font:600 11px var(--mono);color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:2px 6px}
.ws .kb-item{display:block;width:100%;text-align:left;background:none;color:var(--ink);font:400 12.5px var(--mono);padding:4px 6px 4px 14px;border-radius:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ws .kb-item:hover{background:var(--sel)}
.ws .kb-item.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.ws .kb-reader{flex:999 1 480px;min-width:0;border-left:1px solid var(--line);padding-left:18px}
.ws .kb-meta{display:flex;flex-wrap:wrap;gap:4px 14px;font:12px var(--mono);color:var(--dim);border-bottom:1px solid var(--line);padding-bottom:8px}
.ws .kb-path{color:var(--ink);font-weight:600}
.ws .md{font-size:14px;line-height:1.6;overflow-wrap:anywhere}
.ws .md h3{font-size:18px;margin:16px 0 6px}.ws .md h4{font-size:15.5px;margin:14px 0 4px}.ws .md h5,.ws .md h6{font-size:14px;margin:12px 0 4px}
.ws .md p{margin:8px 0}.ws .md ul,.ws .md ol{margin:6px 0;padding-left:22px}
.ws .md code{font:12.5px var(--mono);background:var(--sel);border-radius:4px;padding:1px 4px}
.ws .md pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto}
.ws .md pre code{background:none;padding:0}
.ws .md blockquote{margin:6px 0;padding-left:10px;border-left:3px solid var(--line);color:var(--dim)}
.ws .md hr{border:0;border-top:1px solid var(--line);margin:14px 0}
.ws .kb-foot{border-top:1px solid var(--line);margin-top:12px;padding-top:6px}
.ws .kb-hist{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.ws .kb-hist .on{font-weight:700;text-decoration:none}
.ws .refs button.link{font:12px var(--mono)}
.ws table{width:100%;border-collapse:collapse;font-size:13px}
.ws td,.ws th{padding:5px 8px 5px 0;border-top:1px solid var(--line);text-align:left;vertical-align:top}
.ws th{font:600 12px var(--mono);color:var(--dim)}
.ws td.p{font-family:var(--mono)}
.ws .two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
"""

WS_JS = r"""
(function () {
'use strict';
var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
var root = document.getElementById('ws');
var parts = location.pathname.replace(/\/+$/, '').split('/').filter(Boolean);   // ["workspaces", slug?]
var SLUG = parts.length > 1 ? decodeURIComponent(parts[1]) : null;

function h(tag, attrs) {
  var el = document.createElement(tag);
  attrs = attrs || {};
  Object.keys(attrs).forEach(function (k) {
    if (k === 'text') el.textContent = attrs[k];
    else if (k === 'html') return;
    else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
  });
  // Children: nodes, text, numbers, arrays of those; null, undefined and false (from `cond && el`) are skipped.
  var add = function (x) {
    if (x === null || x === undefined || x === false || x === true) return;
    if (Array.isArray(x)) x.forEach(add);
    else el.appendChild(x instanceof Node ? x : document.createTextNode(String(x)));
  };
  for (var i = 2; i < arguments.length; i++) add(arguments[i]);
  return el;
}
function api(method, path, body) {
  var headers = {'content-type': 'application/json'};
  if (ME && ME.token) headers['Authorization'] = 'Bearer ' + ME.token;
  return fetch(path, {method: method, headers: headers, body: body === undefined ? undefined : JSON.stringify(body)})
    .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); });
}
function note(el, text, ok) { el.textContent = text; el.className = ok ? 'msg-ok' : 'msg-err'; }
function field(label, name, kind, value, extra) {
  var input = kind === 'textarea' ? h('textarea', {name: name}) : h('input', {type: kind || 'text', name: name});
  if (value) input.value = value;
  if (extra) Object.keys(extra).forEach(function (k) { input.setAttribute(k, extra[k]); });
  return h('label', null, label, input);
}
function seedFields() {
  return [h('details', null, h('summary', {text: 'Seed its knowledge base (optional)'}),
    h('p', {'class': 'sub', text: 'What you give here becomes one request in the admin workspace. An admin bot reads it and writes the knowledge base; the workspace is usable right away.'}),
    field('Describe it, in words', 'seed_description', 'textarea'),
    field('Links, one per line', 'seed_links', 'textarea'),
    field('Paste documents or notes', 'seed_text', 'textarea', '', {rows: 8}))];
}
function seedOf(f) {
  var d = f.elements.seed_description.value.trim(), t = f.elements.seed_text.value.trim();
  var links = f.elements.seed_links.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  if (!d && !t && !links.length) return null;
  return {description: d, text: t, links: links};
}
function refsOf(text) {
  return text.split('\n').map(function (s) { return s.trim(); }).filter(Boolean).map(function (s) {
    var m = /^(\S+)\s*(mandatory|\*)?$/.exec(s);
    return {path: m ? m[1] : s, mandatory: !!(m && m[2])};
  });
}
function refsText(refs) { return (refs || []).map(function (r) { return r.path + (r.mandatory ? ' mandatory' : ''); }).join('\n'); }

// ---- the list ----------------------------------------------------------------
function listView() {
  api('GET', '/api/workspaces').then(function (d) {
    // the top bar's Workspaces tab opens the workspace in view; the list is /workspaces?all, from the switcher's last row
    var scope = ''; try { scope = JSON.parse(localStorage.getItem('bb.scope')) || ''; } catch (e) {}
    var moved = /[?&]from=fleet\b/.test(location.search);      // an old /fleet link or bookmark: say where the bots went, once (#751)
    if (scope && !moved && !/[?&]all\b/.test(location.search) && d.workspaces.some(function (w) { return w.slug === scope && !w.archived; })) {
      location.replace('/workspaces/' + encodeURIComponent(scope)); return;
    }
    root.textContent = '';
    root.appendChild(h('h1', {text: 'Workspaces'}));
    if (moved) {
      root.appendChild(h('p', {'class': 'sub', role: 'status', text: 'The bots page moved. Open a workspace: its Team tab starts, stops and shows its bots. Machines and Configuration have their own pages in the top bar.'}));
      history.replaceState(null, '', '/workspaces');
    }
    root.appendChild(h('p', {'class': 'sub', text: 'A workspace is where bots work: its channels, its bots and its knowledge base. A bot sees only its own workspace.'}));
    var mod = moderatorNote(d); if (mod) root.appendChild(mod);
    var cards = h('div', {'class': 'cards'});
    if (!d.workspaces.length) cards.appendChild(h('p', {'class': 'sub', text: 'No workspace yet.'}));
    d.workspaces.forEach(function (w) {
      cards.appendChild(h('div', {'class': 'card'},
        h('h3', null, h('a', {href: '/workspaces/' + encodeURIComponent(w.slug), text: w.name || w.slug}),
          w.archived ? h('span', {'class': 'badge archived', text: 'archived'}) : null),
        h('p', {text: w.purpose || ''}),
        h('div', {'class': 'facts'},
          h('span', null, 'channel ', h('a', {href: '/c/' + encodeURIComponent(w.channel), text: '#' + w.channel})),
          h('span', null, 'bots ', h('b', {text: w.bots.length ? w.bots.map(function (b) { return '@' + b; }).join(', ') : 'none'})))));
    });
    root.appendChild(cards);
    if (d.can_create) root.appendChild(newWorkspace());
    if (!ME) root.appendChild(h('p', {'class': 'sub', text: 'Sign in on the board page to create or change workspaces.'}));
  }).catch(function (e) { root.textContent = ''; root.appendChild(h('p', {'class': 'msg-err', text: e.message})); });
}
function newWorkspace() {
  var out = h('p');
  var f = h('form', null,
    h('div', {'class': 'two'}, field('Slug (its General channel is #slug)', 'slug', 'text', '', {required: '', pattern: '[A-Za-z0-9][A-Za-z0-9._-]{1,63}'}),
      field('Name', 'name', 'text', '', {required: ''})),
    field('Purpose', 'purpose', 'textarea'),
    h('label', {'class': 'check'}, h('input', {type: 'checkbox', name: 'team_setup', checked: ''}),
      ' Set up how its team works together: an admin bot talks it through with you, in a thread in its General channel.'),
    seedFields(), h('button', {type: 'submit', text: 'Create workspace'}), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var body = {slug: f.elements.slug.value.trim(), name: f.elements.name.value.trim(), purpose: f.elements.purpose.value.trim(),
                team_setup: f.elements.team_setup.checked};
    var seed = seedOf(f); if (seed) body.seed = seed;
    api('POST', '/api/workspaces', body).then(function (d) {
      // with the team setup asked for, go to the conversation it started: an admin bot answers there
      location.href = d.workspace.team_thread_id ? '/t/' + d.workspace.team_thread_id : '/workspaces/' + encodeURIComponent(d.workspace.slug);
    })
      .catch(function (e) { note(out, e.message); });
  });
  return h('section', null, h('h2', {text: 'New workspace'}), f);
}

// ---- one workspace ------------------------------------------------------------
// Row 2 of the top bar (#644, #645): the workspace in view, with the same numbers as the board page's switcher
// (what waits on you in each other workspace), and the workspace's tabs.
function workspaceBar(current, tab) {
  var menu = h('ul', {id: 'wsmenu', hidden: ''}), total = h('span', {'class': 'badge', hidden: ''});
  var btn = h('button', {type: 'button', id: 'wsbtn', 'aria-haspopup': 'true', 'aria-expanded': 'false', 'aria-controls': 'wsmenu'}, current, total, ' ▾');
  Promise.all([api('GET', '/api/workspaces'), ME ? api('GET', '/api/status').catch(function () { return {}; }) : {}]).then(function (r) {
    var counts = r[1].counts || {}, others = 0;
    var items = r[0].workspaces.filter(function (x) { return !x.archived || x.slug === current; }).map(function (x) {
      var n = x.slug === current ? 0 : counts[x.slug] || 0; others += n;
      return {slug: x.slug, label: x.name, n: n};
    });
    menu.innerHTML = wsRows(items, current, [{href: '/workspaces?all', text: 'All workspaces'}, {href: '/machines', text: 'Board-wide', cls: 'bw'}]);
    total.textContent = others; total.hidden = !others;
  });
  wsSwitcher(btn, menu, function (w) {
    if (w === current) return;
    try { localStorage.setItem('bb.scope', JSON.stringify(w)); } catch (e) {}
    location.href = '/workspaces/' + encodeURIComponent(w);
  });
  var tabs = h('div', {'class': 'tabs'}, [['team', 'Team'], ['channels', 'Channels and knowledge'], ['activity', 'Activity']].map(function (t) {
    return h('a', {href: '#' + t[0], 'aria-current': t[0] === tab ? 'page' : null, text: t[1]});
  }));
  return h('nav', {'class': 'l2', 'aria-label': 'workspace'}, h('span', {'class': 'lab', text: 'Workspace'}), h('div', {'class': 'wsw'}, btn, menu), tabs);
}
function wsView() {
  api('GET', '/api/workspaces/' + encodeURIComponent(SLUG)).then(function (d) {
    var w = d.workspace, shown = null;
    var draw = function () {
      var tab = location.hash === '#channels' ? 'channels' : location.hash === '#activity' ? 'activity' : 'team';
      if (tab === shown) return; shown = tab;
      root.textContent = '';
      root.appendChild(workspaceBar(w.slug, tab));
      root.appendChild(h('h1', null, w.name || w.slug, w.archived ? h('span', {'class': 'badge archived', text: 'archived'}) : null));
      if (w.purpose) root.appendChild(h('p', {'class': 'sub', text: w.purpose}));
      if (tab === 'team') { teamView(w); return; }
      if (tab === 'activity') { activityView(w); return; }
      root.appendChild(knowledgeNote(w));
      root.appendChild(channelsSection(w));
      root.appendChild(knowledgeSection(w));
      if (w.can_manage && !w.archived) root.appendChild(settingsSection(w));
      if (w.can_manage && w.archived && w.slug !== 'admin') root.appendChild(deleteSection(w));
    };
    window.addEventListener('hashchange', draw); draw();
  }).catch(function (e) { root.textContent = ''; root.appendChild(h('p', {'class': 'msg-err', text: e.message})); });
}
// The Activity tab (#738, option A): what happened in the last 24 hours, as counts and one feed. A click on a count
// narrows the feed to that kind. Everything from the forge is put on the page as text, and a link is made only when its
// address starts with http, https or /: a forge cannot send a script: address into a href.
var ACT_STATS = [['merged', 'PRs merged'], ['opened', 'PRs and issues opened'], ['closed', 'issues closed'], ['review', 'reviews'],
                 ['commit', 'commits'], ['thread', 'threads started or completed'], ['message', 'messages'], ['bots', 'bots active']];
var ACT_ICON = {merged: '\u2713', opened: '+', closed: '\u2713', review: '\u25cf', commit: '\u2325', thread: '\u2022'};
function safeHref(u) { u = u || ''; return /^(https?:\/\/|\/(?![\/\\]))/.test(u) && !/[\\\u0000-\u001f\u007f]/.test(u) ? u : null; }
function dayName(t) {      // "Today", "Yesterday", else the date: a 24-hour window spans two days
  var midnight = function (x) { return new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime(); };
  var days = Math.round((midnight(new Date()) - midnight(t)) / 86400000);
  return days <= 0 ? 'Today' : days === 1 ? 'Yesterday' : t.toLocaleDateString([], {month: 'short', day: 'numeric'});
}
function activityView(w) {
  var sec = h('section', {'class': 'activity'}), body = h('div', {'aria-live': 'polite'}), filter = null;
  sec.appendChild(h('h2', null, 'Activity'));
  sec.appendChild(body);
  root.appendChild(sec);
  var load = function () {
    body.textContent = '';
    body.appendChild(h('p', {'class': 'sub', 'aria-busy': 'true', text: 'Loading the last 24 hours\u2026'}));
    api('GET', '/api/workspaces/' + encodeURIComponent(w.slug) + '/activity?hours=24').then(function (d) { draw(d.activity); })
      .catch(function (e) {
        body.textContent = '';
        var retry = h('button', {type: 'button', 'class': 'quiet', text: 'Retry'}); retry.addEventListener('click', load);
        body.appendChild(h('p', {'class': 'msg-err', role: 'alert'}, e.message + ' ', retry));
      });
  };
  var draw = function (a) {
    body.textContent = '';
    var since = new Date(a.since);
    body.appendChild(h('p', {'class': 'sub', title: since.toISOString(), text: 'Since ' + since.toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) + ' \u00b7 last ' + a.hours + ' hours'}));
    if (a.forge === 'none') {
      body.appendChild(h('div', {'class': 'warn', role: 'status'}, h('b', null, 'No repository is linked to ' + (w.name || w.slug) + '. '),
        'Pull requests, issues, reviews and commits are not shown; the counts below are from the board only. ', h('a', {href: '#team', text: 'A manager can have the board create one on the Team tab.'})));
    } else if (a.forge === 'unset') {
      body.appendChild(h('div', {'class': 'warn', role: 'status'}, h('b', null, 'The board has no forge address or token, so it cannot read the repository. '),
        'Showing threads and messages from the board only. Set FORGEJO_URL (the forge\u2019s address) and FORGEJO_TOKEN (a token that can read it) under Shared secrets on the ', h('a', {href: '/configuration', text: 'Configuration page'}), '.'));
    } else if (a.forge === 'down') {
      var retry = h('button', {type: 'button', 'class': 'quiet', text: 'Retry'}); retry.addEventListener('click', load);
      body.appendChild(h('div', {'class': 'warn', role: 'alert'}, h('b', null, 'The forge did not answer. '),
        'Showing threads and messages from the board only. Pull requests, issues, reviews and commits will appear when it answers. ', retry,
        h('details', null, h('summary', {text: 'Details'}), h('span', {'class': 'sub', text: a.reason}))));
    }
    if (!a.events.length && !a.counts.message) {
      body.appendChild(h('div', {'class': 'empty'}, h('h3', {text: 'Nothing in the last ' + a.hours + ' hours'}),
        h('p', {text: 'No ' + (a.forge === 'ok' ? 'pull request, issue, commit, ' : '') + 'thread or message in ' + (w.name || w.slug) + ' in this time.'})));
    } else {
      var strip = h('div', {'class': 'actstrip'}), feed = h('ul', {'class': 'actfeed'});
      var fill = function () {
        feed.textContent = '';
        var shown = a.events.filter(function (e) { return !filter || e.kind === filter; }), day = '';
        shown.forEach(function (e) {
          var href = safeHref(e.url), when = new Date(e.at), d = dayName(when);
          if (d !== day) { day = d; feed.appendChild(h('li', {'class': 'day', text: d})); }
          feed.appendChild(h('li', {'class': 'ev ' + e.kind},
            h('time', {datetime: e.at, title: when.toISOString(), text: when.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}),
            h('span', {'class': 'ic', 'aria-hidden': 'true', text: ACT_ICON[e.kind] || '\u2022'}),
            h('div', null, href ? h('a', {href: href, rel: 'noopener noreferrer', text: e.text}) : h('span', {text: e.text}),
              h('div', {'class': 'sub', text: e.by ? '@' + e.by : 'author not named'}))));
        });
        if (filter) feed.insertBefore(h('li', {'class': 'sub', text: 'Showing ' + shown.length + ' of ' + a.events.length + '.'}), feed.firstChild);
        if (!shown.length) feed.appendChild(h('li', {'class': 'sub', text: filter ? 'Nothing of that kind in this time.' : 'Nothing to list.'}));
      };
      ACT_STATS.forEach(function (s) {
        if (a.counts[s[0]] === undefined) return;       // the forge's counts are absent, not zero, when the forge is
        var n = h('b', {text: String(a.counts[s[0]])}), label = h('span', {text: s[1]});
        if (a.events.some(function (e) { return e.kind === s[0]; })) {
          var b = h('button', {type: 'button', 'class': 'stat', 'aria-pressed': String(filter === s[0])}, n, label);
          b.addEventListener('click', function () {
            filter = filter === s[0] ? null : s[0];
            Array.prototype.forEach.call(strip.querySelectorAll('button'), function (x) { x.setAttribute('aria-pressed', 'false'); });
            if (filter) b.setAttribute('aria-pressed', 'true');
            fill();
          });
          strip.appendChild(b);
        } else strip.appendChild(h('div', {'class': 'stat'}, n, label));
      });
      body.appendChild(strip); body.appendChild(feed); fill();
    }
    body.appendChild(h('p', {'class': 'sub', text: (a.forge === 'ok' ? 'Pull requests, issues, reviews and commits come from ' + a.repository + ' on the forge; threads and messages come from the board.' + (a.capped ? ' This repository is busy: the forge lists are cut at fifty items, so the counts are at least these.' : '')
      : 'Threads and messages come from the board.')}));
  };
  load();
}
// The Team tab (#645): the agreement first, then the bots under the roles the agreement names.
// The workspace's repository (#715): its link when it has one, else a manager can have the board create it.
function repositoryCard(w) {
  var sec = h('section', {'class': 'repo'}, h('h2', null, 'Repository')), out = h('p', {'class': 'sub', role: 'status'});
  var canAct = !!(ME && w.can_manage && !w.archived);
  var url = '/api/workspaces/' + encodeURIComponent(w.slug) + '/repository';
  // the field and its button: link a repository by name (#743), also used to change the link (#752)
  var linkForm = function (label) {
    var name = h('input', {type: 'text', autocomplete: 'off', 'aria-label': 'Repository to link, owner/name', placeholder: 'owner/name, for example jac/bot_board', 'class': 'mono', disabled: canAct ? null : ''});
    var link = h('button', {type: 'button', text: label, disabled: ''}), linkOut = h('p', {'class': 'sub'});
    name.addEventListener('input', function () { link.disabled = !canAct || !/^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/.test(name.value.trim()); });
    var send = function () {
      if (link.disabled) return;
      link.disabled = true; linkOut.setAttribute('role', 'status'); note(linkOut, 'Linking…', true);
      api('PUT', url, {repository: name.value.trim()})
        .then(function () { location.reload(); }, function (e) { link.disabled = false; linkOut.setAttribute('role', 'alert'); note(linkOut, e.message); });
    };
    link.addEventListener('click', send);
    name.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') send(); });
    return h('div', null, h('p', {'class': 'linkrow'}, name, link), linkOut);
  };
  if (w.repository) {
    sec.appendChild(h('p', null, w.repository_url ? h('a', {href: w.repository_url, 'class': 'mono', text: w.repository}) : h('span', {'class': 'mono', text: w.repository})));
    // the routine that offers its new issues to the bots and reposts the stuck ones (#809): said once, linked, so a person can pause it
    if (w.issue_routine) sec.appendChild(h('p', {'class': 'sub'}, 'New issues are offered to the bots, and stuck ones are posted again, by ',
      h('a', {href: '/routines#r=' + w.issue_routine, text: 'this routine'}), '. Pause or delete it there.'));
    if (!canAct) { sec.appendChild(h('p', {'class': 'sub', text: 'Only a manager of this workspace, signed in on the board page, can change or unlink the repository.'})); return sec; }
    // Change opens the same field; Unlink asks first, since the workspace then has no repository for its bots and routines
    var change = h('details', {'class': 'repo-change'}, h('summary', {text: 'Change repository'}), linkForm('Link repository'));
    var unlink = h('button', {type: 'button', 'class': 'quiet danger', text: 'Unlink'}), ask = h('p', {'class': 'sub', hidden: ''});
    var yes = h('button', {type: 'button', 'class': 'quiet danger', text: 'Unlink ' + w.repository}), no = h('button', {type: 'button', text: 'Cancel'});
    ask.appendChild(document.createTextNode('The workspace stops using it: its bots read no repository from here, and the repository stays on the forge. '));
    ask.appendChild(yes); ask.appendChild(document.createTextNode(' ')); ask.appendChild(no);
    unlink.addEventListener('click', function () { ask.hidden = false; unlink.hidden = true; no.focus(); });
    no.addEventListener('click', function () { ask.hidden = true; unlink.hidden = false; unlink.focus(); });
    yes.addEventListener('click', function () {
      yes.disabled = true; out.setAttribute('role', 'status'); note(out, 'Unlinking…', true);
      api('PUT', url, {repository: ''})
        .then(function () { location.reload(); }, function (e) { yes.disabled = false; out.setAttribute('role', 'alert'); note(out, e.message); });
    });
    sec.appendChild(change); sec.appendChild(h('p', null, unlink)); sec.appendChild(ask); sec.appendChild(out);
    return sec;
  }
  var btn = h('button', {type: 'button', text: 'Create repository', disabled: canAct ? null : '', title: canAct ? null : 'Only a manager of this workspace, signed in on the board page'});
  btn.addEventListener('click', function () {
    btn.disabled = true; note(out, 'Asking the forge…', true);
    api('POST', url)
      .then(function () { location.reload(); }, function (e) { btn.disabled = false; note(out, e.message); });
  });
  sec.appendChild(h('p', {'class': 'sub', text: 'This workspace has no repository yet. The board creates an empty one on the forge, named after the workspace.'}));
  sec.appendChild(h('p', null, btn));
  if (!canAct) sec.appendChild(h('p', {'class': 'sub', text: 'Only a manager of this workspace, signed in on the board page, can create or link a repository.'}));
  sec.appendChild(h('p', {'class': 'sub', text: 'Or link a repository that already exists on the forge.'}));
  sec.appendChild(linkForm('Link repository'));
  sec.appendChild(out);
  return sec;
}
function teamView(w) {
  var slot = h('div'); root.appendChild(slot);
  Promise.all([
    api('GET', '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge/team/agreement.md').then(function (d) { return d.document; }, function () { return null; }),
    api('GET', '/api/workspaces/' + encodeURIComponent(w.slug) + '/team').then(function (d) { return d.team; }, function () { return {roles: []}; }),
    api('GET', '/api/fleet').then(function (d) { return d.hosts; }, function () { return []; }),
    proposalDocs(w),
    agreementRevision(w),
    api('GET', '/api/workspaces').then(function (d) { return d.moderator || {}; }, function () { return {}; })
  ]).then(function (r) {
    slot.appendChild(repositoryCard(w));
    slot.appendChild(agreementCard(w, r[0]));
    slot.appendChild(changeBox(w, r[0] ? r[0].revision : r[4], r[5]));
    if (r[3].length) slot.appendChild(proposalsView(w, r[0], r[3], r[4]));
    slot.appendChild(rolesView(w, r[1].roles || [], r[2]));
  });
}
// A proposed change to the agreement (#646): the admin bot saves the whole proposed text as team/proposals/<thread id>.md,
// with `<!-- base: <revision> -->` on its first line. The page, not the bot, computes the difference; only a human's Apply writes
// the agreement, with the text this page showed.
function parseProposal(body) {      // the one place the base revision is read; a missing or malformed one is no base
  var m = /^<!--\s*base:\s*(\d+)\s*-->[ \t]*\r?\n?/.exec(body);
  var crlf = function (t) { return t.replace(/\r\n/g, '\n'); };      // the server stores a Windows line ending as a newline
  return m ? {base: parseInt(m[1], 10), text: crlf(body.slice(m[0].length))} : {base: null, text: crlf(body)};
}
// The revision a first agreement is written over: 0 for a workspace that never had one, else the last revision of one that was deleted.
function agreementRevision(w) {
  return api('GET', '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge/team/agreement.md/history').then(function (d) {
    return d.revisions.reduce(function (m, x) { return Math.max(m, x.revision); }, 0);
  }, function () { return 0; });
}
// "Change the agreement" (#646): the words go to the workspace's General channel as a thread that mentions the admin bot, which
// drafts a proposal (the team-setup skill). Nothing changes until a human applies it on this tab.
function changeBox(w, rev, admin) {
  var canAct = !!(ME && w.can_manage && !w.archived);
  var sec = h('section', {'class': 'changebox'}, h('h2', {text: 'Change the agreement'}));
  if (!canAct) { sec.appendChild(h('p', {'class': 'sub', text: 'Only a manager of this workspace, signed in on the board page, can ask for a change.'})); return sec; }
  var text = h('textarea', {rows: '3', maxlength: '4000', 'aria-label': 'what should change', placeholder: 'For example: add a marketer who writes release notes, or: only ask security about security-sensitive changes'});
  var go = h('button', {type: 'submit', text: 'Draft it'}), out = h('p', {role: 'status'});
  var form = h('form', null, text, h('div', {'class': 'dlg-actions'}, go), out);
  if (!admin.handle || !admin.running) sec.appendChild(h('p', {'class': 'warn', text: admin.handle ? 'The admin bot @' + admin.handle + ' is stopped, so nobody will read the request until it runs.' : 'There is no admin bot, so nobody will read the request.'}));
  sec.appendChild(h('p', {'class': 'sub', text: 'Say what you want in plain words. The admin bot asks what it cannot guess, then saves a proposal here. Nothing changes until you apply it.'}));
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var words = text.value.trim(); if (!words) return;
    go.disabled = true;
    api('POST', '/api/messages', {channel: w.channel, body: 'Amendment request for ' + w.slug + ', agreement revision ' + rev + ':\n' + words + (admin.handle ? '\n\n@' + admin.handle : '')}).then(function (d) {
      var warn = (d.warnings || []).map(function (x) { return x.text || ('@' + x.mention + ' is ' + x.state + ', so nobody will read it yet.'); }).join(' ');
      out.className = 'msg-ok'; out.textContent = ''; out.appendChild(document.createTextNode('Asked. The conversation is in '));
      out.appendChild(h('a', {href: '/t/' + d.message.id, text: 'thread #' + d.message.id})); out.appendChild(document.createTextNode('.' + (warn ? ' ' + warn : '')));
      text.value = ''; go.disabled = false;
    }).catch(function (e) { go.disabled = false; note(out, e.message); });
  });
  sec.appendChild(form);
  return sec;
}
function proposalDocs(w) {
  var base = '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge/';
  return api('GET', '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge').then(function (d) {
    var docs = d.documents.filter(function (x) { return /^team\/proposals\/\d+\.md$/.test(x.path); });
    return Promise.all(docs.map(function (x) {
      return api('GET', base + x.path).then(function (r) {
        var q = parseProposal(r.document.body);
        return {path: x.path, tid: parseInt(x.path.replace(/\D/g, ''), 10), revision: r.document.revision, author: r.document.author, base: q.base, text: q.text};
      });
    })).then(function (list) { return list.sort(function (a, b) { return b.tid - a.tid; }); });      // newest first
  }, function () { return []; });
}
// Characters a reader cannot see, that could hide an instruction in a line that looks clean. A list never finishes, so the rule is the
// class (#707): every character of Unicode category Cc, Cf, Cn, Co, Cs, Zl or Zp except a newline and a tab, plus the few in none of them that show nothing
// (the combining grapheme joiner, the Hangul fillers, the blank braille pattern, the variation selectors, the tag characters). The
// server refuses the same set (workspaces.py, hidden_char). The agreement reaches every bot, so a proposal with one is shown with the
// character spelled out and cannot be applied.
var HIDDEN = /(?![\n\t])[\p{Cc}\p{Cf}\p{Cn}\p{Co}\p{Cs}\p{Zl}\p{Zp}]|[\u034F\u115F\u1160\u17B4\u17B5\u180B-\u180D\u180F\u2800\u3164\uFE00-\uFE0F\uFFA0]|[\u{E0000}-\u{E007F}\u{E0100}-\u{E01EF}]/gu;
function showHidden(line) { return line.replace(HIDDEN, function (c) { return '<U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') + '>'; }); }
function lineDiff(a, b) {
  var x = a.split('\n'), y = b.split('\n');
  if (x.length * y.length > 4000000) return x.map(function (l) { return {t: 'del', s: l}; }).concat(y.map(function (l) { return {t: 'add', s: l}; }));
  var n = x.length, m = y.length, t = [], i, j;
  for (i = 0; i <= n; i++) t.push(new Array(m + 1).fill(0));
  for (i = n - 1; i >= 0; i--) for (j = m - 1; j >= 0; j--) t[i][j] = x[i] === y[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1]);
  var out = []; i = 0; j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push({t: 'ctx', s: x[i]}); i++; j++; }
    else if (t[i + 1][j] >= t[i][j + 1]) { out.push({t: 'del', s: x[i]}); i++; }
    else { out.push({t: 'add', s: y[j]}); j++; }
  }
  while (i < n) out.push({t: 'del', s: x[i++]});
  while (j < m) out.push({t: 'add', s: y[j++]});
  return out;
}
function diffView(oldText, newText) {
  var lines = lineDiff(oldText, newText), keep = lines.map(function () { return false; });
  lines.forEach(function (l, i) { if (l.t !== 'ctx') for (var k = Math.max(0, i - 2); k <= Math.min(lines.length - 1, i + 2); k++) keep[k] = true; });
  var pre = h('pre', {'class': 'diff', tabindex: '0', 'aria-label': 'what the proposal changes'}), gap = false;
  lines.forEach(function (l, i) {
    if (!keep[i]) { gap = true; return; }
    if (gap) { pre.appendChild(h('span', {'class': 'gap', text: '…\n'})); gap = false; }
    pre.appendChild(h('span', {'class': l.t, text: (l.t === 'add' ? '+ ' : l.t === 'del' ? '- ' : '  ') + showHidden(l.s) + '\n'}));
  });
  return pre;
}
function threadReply(w, tid, text) {      // the outcome, in the proposal's own thread, as the person who pressed the button
  return api('GET', '/api/messages/' + tid).then(function (d) {
    if (!w.channels.some(function (c) { return c.slug === d.message.channel; })) throw new Error('Thread #' + tid + ' is not in this workspace, so nothing was written or posted.');
    return api('POST', '/api/messages', {channel: d.message.channel, reply_to: tid, body: text});
  });
}
function proposalsView(w, doc, proposals, goneRev) {
  var canAct = !!(ME && w.can_manage && !w.archived), base = '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge/';
  var sec = h('section', {'class': 'proposals'}, h('h2', null, 'Proposed changes', h('small', {text: String(proposals.length)})));
  if (proposals.length > 1) sec.appendChild(h('p', {'class': 'sub', text: 'Applying one proposal makes the others out of date: they were drafted against the revision before it, and can only be rejected.'}));
  if (!canAct) sec.appendChild(h('p', {'class': 'sub', text: 'Only a manager of this workspace, signed in on the board page, can apply or reject.'}));
  proposals.forEach(function (p) {
    var rev = doc ? doc.revision : goneRev, stale = p.base === null || p.base !== rev, hidden = p.text.search(HIDDEN) >= 0, out = h('p', {role: 'status'});
    var card = h('div', {'class': 'proposal'},
      h('h3', null, h('a', {href: '/t/' + p.tid, text: 'Proposal from thread #' + p.tid}), p.author ? h('small', {text: ' saved by @' + p.author}) : null),
      h('p', {'class': 'sub'}, 'The admin bot explains it in ', h('a', {href: '/t/' + p.tid, text: 'its thread'}), '.'));
    card.appendChild(diffView(doc ? doc.body.replace(/\r\n/g, '\n') : '', p.text));      // an agreement stored before CRLF was normalised
    if (stale) card.appendChild(h('p', {'class': 'warn', text: p.base === null ? 'It names no revision it was drafted against, so it cannot be applied. Reject it and ask for a new one.'
      : 'Out of date: it was drafted against revision ' + p.base + ', and the agreement is at revision ' + rev + '. It can only be rejected.'}));
    if (hidden) card.appendChild(h('p', {'class': 'warn', text: 'The proposal contains characters a reader cannot see (shown as <U+…> in the difference). It cannot be applied: reject it and ask for a clean one.'}));
    if (doc && /^\|\s*Role\s*\|/mi.test(doc.body) && !/^\|\s*Role\s*\|/mi.test(p.text))
      card.appendChild(h('p', {'class': 'warn', text: 'The agreement has a Roles table and this proposal has none. Applying it would remove the roles the Team tab reads.'}));
    var apply = h('button', {type: 'button', text: 'Apply', disabled: canAct && !stale && !hidden ? null : '', title: canAct ? (stale ? 'Out of date' : hidden ? 'Has invisible characters' : null) : 'Only a manager of this workspace, signed in on the board page'});
    var reject = h('button', {type: 'button', 'class': 'quiet danger', text: 'Reject', disabled: canAct ? null : ''});
    var drop = function () { return api('DELETE', base + p.path, {base_revision: p.revision, reason: 'applied or rejected'}); };
    apply.addEventListener('click', function () {
      apply.disabled = reject.disabled = true;
      // read both again at press time: what is written is what this page showed, or nothing
      Promise.all([api('GET', base + p.path), api('GET', base + 'team/agreement.md').catch(function () { return agreementRevision(w).then(function (n) { return {document: {revision: n}}; }); })]).then(function (r) {
        var q = parseProposal(r[0].document.body);
        if (q.text !== p.text || q.base !== p.base || r[1].document.revision !== rev) { note(out, 'The proposal or the agreement changed since this page loaded. Reload to see the current difference.'); return; }
        if (q.text.search(HIDDEN) >= 0) { note(out, 'The proposal contains invisible characters and cannot be applied.'); return; }
        return api('GET', '/api/messages/' + p.tid).then(function (m) {
          if (!w.channels.some(function (c) { return c.slug === m.message.channel; })) throw new Error('Thread #' + p.tid + ' is not in this workspace, so nothing was written.');
        }).then(function () { return api('PUT', base + 'team/agreement.md', {body: p.text, base_revision: rev, reason: 'Applied the proposal from thread ' + p.tid, sources: [String(p.tid)]}); })
          .then(function (d) { return threadReply(w, p.tid, 'Applied. The team agreement is now revision ' + d.document.revision + '.'); })
          .then(drop).then(function () { note(out, 'Applied.', true); setTimeout(function () { location.reload(); }, 1500); });
      }).catch(function (e) { apply.disabled = reject.disabled = false; note(out, e.message + (/revision|409/.test(e.message) ? ' Someone changed the agreement: reload and ask the admin bot for a new proposal.' : '')); });
    });
    reject.addEventListener('click', function () {
      dialog('Reject the proposal', [h('p', {text: 'Reject the proposal from thread #' + p.tid + '? The agreement stays as it is, and the thread is told.'})], [{text: 'Reject', danger: true, run: function (el, close, fail) {
        el.disabled = true;
        threadReply(w, p.tid, 'Rejected. The team agreement is unchanged.').then(drop).then(function () { close(); location.reload(); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
    });
    card.appendChild(h('div', {'class': 'dlg-actions'}, reject, apply)); card.appendChild(out); sec.appendChild(card);
  });
  return sec;
}
function agreementCard(w, doc) {
  if (!doc) return h('section', null, h('h2', {text: 'Team agreement'}), h('p', {'class': 'sub', text: 'This workspace has no team agreement yet. Bots work without one, and nothing says who reviews or who merges.'}));
  var open = false, box = h('div', {'class': 'agr'}), more = h('button', {type: 'button', 'class': 'quiet', 'aria-expanded': 'false', text: 'Read the whole agreement'});
  doc.body.split(/\n(?=#+ )/).forEach(function (part) {
    var m = part.match(/^#+ +(.*)\n?([\s\S]*)$/); if (!m || /^team agreement$/i.test(m[1].trim()) && !m[2].trim() || /^roles$/i.test(m[1].trim())) return;      // the Roles table is the bots list below
    box.appendChild(h('h4', {text: m[1].trim()})); box.appendChild(h('div', {'class': 'agr-text', text: m[2].trim()}));
  });
  box.classList.add('short');
  more.addEventListener('click', function () { open = !open; box.classList.toggle('short', !open); more.setAttribute('aria-expanded', String(open)); more.textContent = open ? 'Show less' : 'Read the whole agreement'; });
  return h('section', null, h('h2', null, 'Team agreement', h('small', {text: 'revision ' + doc.revision + (doc.author ? ', last edited by ' + doc.author : '') + (doc.created_at ? ', ' + doc.created_at.slice(0, 10) : '')})), box, more);
}
function ago(iso) {
  var t = iso && Date.parse(iso); if (!t) return '—';
  var s = Math.max(0, (Date.now() - t) / 1000);
  return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago';
}
// A modal dialog: a title, some nodes, buttons. Escape and the Cancel button close it, and focus returns to what opened it.
function dialog(title, nodes, buttons) {
  var opener = document.activeElement, ov = h('div', {'class': 'ovl'}), box = h('div', {'class': 'dlg', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'dlgt'});
  var close = function () { document.removeEventListener('keydown', onKey); ov.remove(); if (opener && opener.focus) opener.focus(); };
  var err = h('p', {'class': 'msg-err', role: 'alert'});
  // Escape closes; Tab stays inside the dialog (aria-modal does not do that by itself)
  var onKey = function (ev) {
    if (ev.key === 'Escape') { close(); return; }
    if (ev.key !== 'Tab') return;
    var f = Array.prototype.filter.call(box.querySelectorAll('button, select, input, a[href]'), function (x) { return !x.disabled && !x.hidden; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    else if (!box.contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
  };
  box.appendChild(h('h3', {id: 'dlgt', text: title}));
  nodes.forEach(function (n) { box.appendChild(n); });
  box.appendChild(err);
  var cancel = h('button', {type: 'button', 'class': 'quiet', text: 'Cancel'});
  var row = h('div', {'class': 'dlg-actions'}, cancel);
  cancel.addEventListener('click', close);
  var fail = function (msg) { err.textContent = msg; };      // an error stays in the dialog, where the person is looking
  buttons.forEach(function (bt) {
    var el = h('button', {type: 'button', 'class': bt.danger ? 'danger' : '', text: bt.text});
    el.addEventListener('click', function () { err.textContent = ''; bt.run(el, close, fail); });
    row.appendChild(el);
    if (bt.init) bt.init(el);
  });
  box.appendChild(row); ov.appendChild(box); root.appendChild(ov);      // inside the page, so its button rules reach the dialog
  ov.addEventListener('click', function (ev) { if (ev.target === ov) close(); });
  document.addEventListener('keydown', onKey);
  // a destructive dialog starts on Cancel, so Enter does not delete
  var danger = buttons.some(function (bt) { return bt.danger; });
  (danger ? cancel : box.querySelector('select, input') || row.lastChild).focus();
  return close;
}
// A host bot's block (#733, from the bots page's #595): how it runs, who set it and when, why, whether its Mac listed it, its apps
// (a warning for one the Mac does not have), and the two things a manager does: edit the reason and apps, move it back to a container.
function hostBlock(b, ctx) {
  var box = (ctx.allHosts || []).filter(function (x) { return x.name === b._host; })[0] || {}, silent = !box.reporting, url = '/api/fleet/bots/' + encodeURIComponent(b.handle) + '/host';
  var lc = function (x) { return String(x).toLowerCase(); };
  var listed = silent || (box.host_allowed || []).map(lc).indexOf(lc(b.handle)) >= 0;
  var found = {}; (box.apps || []).forEach(function (a) { if (a && a.name) found[lc(a.name)] = a.version || ''; });
  var runs = b.on_host && b.runmode === 'host' ? 'on its Mac, outside a container' : b.on_host ? 'in a container now, and on its Mac from its next start'
    : 'in a container (its Mac\'s host switch is off, or it moved)';
  var d = h('div', {'class': 'd-host'}, h('h4', {text: 'Runs on the host'}));
  d.appendChild(h('div', {'class': 'd-facts'}, h('span', null, 'runs ', h('b', {text: runs})),
    h('span', null, 'set by ', h('b', {text: b.host_by || '?'}), b.host_at ? ' ' + ago(b.host_at) : ''),
    silent ? h('span', {'class': 'warn'}, 'its Mac is not reporting, so whether it is listed and which apps it has are not known')
           : listed ? h('span', null, 'listed in the Mac\'s host-bots-allowed file ', h('b', {text: 'yes'})) : null));
  if (!listed) d.insertBefore(h('p', {'class': 'warn'}, 'Waiting for you: add the line ', h('b', {text: b.handle}), ' to ~/.config/bot_board/host-bots-allowed on the Mac.'), d.firstChild.nextSibling);
  d.appendChild(h('p', {'class': 'sub clamp'}, h('b', {text: 'Why it needs the host: '}), b.host_reason || ''));
  if ((b.host_apps || []).length) d.appendChild(h('ul', {'class': 'd-apps'}, b.host_apps.map(function (n) {
    var v = found[lc(n)];
    return silent ? h('li', null, n) : v === undefined ? h('li', {'class': 'warn'}, n + ' ', h('b', {text: 'not found in /Applications on this Mac'})) : h('li', null, n + ' ', h('b', {text: v}));
  })));
  var acts = h('div', {'class': 'd-acts'});
  var act = function (text, fn, cls) {
    var el = h('button', {type: 'button', 'class': cls || 'quiet', text: text, disabled: ctx.canAct ? null : '', title: ctx.canAct ? null : 'Only a manager of this workspace, signed in on the board page'});
    el.addEventListener('click', fn); acts.appendChild(el);
  };
  act('edit why and apps…', function () {
    var why = h('input', {type: 'text', maxlength: '200', 'aria-label': 'why it needs the host'}), apps = h('input', {type: 'text', 'aria-label': 'apps'});
    why.value = b.host_reason || ''; apps.value = (b.host_apps || []).join(', ');
    dialog('Host details of @' + b.handle, [h('label', null, 'Why does it need the host? One line.', why), h('label', null, 'The apps it uses, separated by commas', apps)],
      [{text: 'Save', run: function (el, close, fail) {
        el.disabled = true;
        api('PUT', url, {reason: why.value.trim(), apps: apps.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean)})
          .then(function () { close(); ctx.said('Host details of @' + b.handle + ' saved.', true); ctx.again(); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  });
  act('move back to a container…', function () {
    dialog('Move @' + b.handle + ' back to a container?', [h('p', {text: 'It runs there from its next start and keeps its conversation where its harness allows. This cannot be undone: to run it on the host again, start a new host bot.'})],
      [{text: 'Move it back', danger: true, run: function (el, close, fail) {
        el.disabled = true;
        api('DELETE', url).then(function () { close(); ctx.said('@' + b.handle + ' moves back to a container at its next start.', true); ctx.again(); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  }, 'quiet danger');
  d.appendChild(acts);
  return d;
}
// Stopping or killing a bot that holds open threads asks what happens to them (#733): hand them off once it has stopped (the default,
// since a stopped bot that keeps its threads holds them from everyone, #217), keep them, or retire it (#167). The API takes
// `threads: handoff|retire`; keep sends nothing.
function stopDialog(b, kill, ctx) {
  var n = b.open_threads || 0, name = 'threads-' + b.handle, verb = kill ? 'Kill' : 'Stop';
  var opt = function (v, label, note, on) {
    var i = h('input', {type: 'radio', name: name, value: v}); if (on) i.checked = true;
    return h('label', {'class': 'check'}, i, ' ', h('b', {text: label}), ' ', h('span', {'class': 'dim', text: note}));
  };
  var opts = [opt('handoff', 'Hand them off', 'released once it has stopped; the next bot that watches claims each', true),
              opt('keep', 'Keep them', 'for a planned restart or a move; nobody else can pick them up meanwhile'),
              opt('retire', 'Retire', 'hand them off, and drop it from the stopped list')];
  var intro = '@' + b.handle + ' holds ' + n + ' open thread' + (n === 1 ? '' : 's') + '. What should happen to ' + (n === 1 ? 'it' : 'them') + ' when it stops?';
  dialog(verb + ' @' + b.handle + '?', [h('p', {text: intro})].concat(kill ? [h('p', {'class': 'warn', text: 'Kill stops it now: the run it is in is cut short and its reply is lost.'})] : [], opts),
    [{text: verb, danger: !!kill, run: function (el, close, fail) {
      var choice = opts.map(function (l) { return l.firstChild; }).filter(function (i) { return i.checked; })[0].value;
      var body = {host: b._host, run: false, harness: b.harness || null};
      if (kill) body.kill = true;
      if (choice !== 'keep') body.threads = choice;
      el.disabled = true;
      api('PUT', '/api/fleet/bots/' + encodeURIComponent(b.handle), body)
        .then(function () { close(); ctx.said(verb + ' asked for @' + b.handle + (choice === 'keep' ? ', keeping its threads.' : choice === 'retire' ? ', handing its threads off and retiring it.' : ', handing its threads off.'), true); ctx.again(); })
        .catch(function (e) { el.disabled = false; fail(e.message); });
    }}]);
}
// A bot's drawer (#18527): facts, the threads it holds, and one action for each thing a manager changes. Each calls the endpoint the
// bots page used, and asks again before anything that cannot be undone.
function drawer(b, up, ctx) {
  var st = b.state || {}, w = ctx.w, d = h('div', {'class': 'drawer-body'});
  var facts = [['session', b.session_started ? 'started ' + ago(b.session_started) : ''], ['launcher', b.launcher || ''], ['last run', b.last_run ? ago(b.last_run) : ''],
               ['last post', b.last_post ? ago(b.last_post) : ''], ['role', b.profile || 'none'], ['channels', (b.channels || []).length ? b.channels.map(function (c) { return '#' + c; }).join(', ') : 'any'],
               ['takes new work', b.claims === 'mentioned' ? 'no, works when mentioned' : 'yes']].filter(function (x) { return x[1]; });
  var why = [b.problem, st.reason, up ? b.health : ''].filter(Boolean);
  why.forEach(function (t) { d.appendChild(h('p', {'class': 'warn', text: t})); });      // what is wrong comes first
  d.appendChild(h('div', {'class': 'd-facts'}, facts.map(function (x) { return h('span', null, x[0] + ' ', h('b', {text: x[1]})); })));
  var vtext = function (label, v, dflt) { return h('p', {'class': 'sub clamp'}, h('b', {text: label + ': '}), v ? v : h('span', {'class': 'dim', text: 'the fleet default, until set'})); };
  d.appendChild(vtext('Voice', b.voice)); d.appendChild(vtext('Personality', b.personality));
  var held = b.open_threads || 0, list = b.open_thread_list || [];
  if (held) {
    var ul = h('ul', {'class': 'd-threads'}, list.slice(0, 3).map(function (t) { return h('li', null, h('a', {href: '/t/' + t.thread_id, text: '#' + t.channel + ' · ' + (t.title || '(untitled)')}), ' ', h('span', {'class': 'dim', text: ago(t.last_at)})); }));
    if (held > Math.min(list.length, 3)) ul.appendChild(h('li', {'class': 'dim', text: 'and ' + (held - Math.min(list.length, 3)) + ' more'}));
    d.appendChild(h('p', {'class': 'sub', text: 'Holds ' + held + ' open thread' + (held === 1 ? '' : 's') + ':'})); d.appendChild(ul);
  }
  var url = '/api/fleet/bots/' + encodeURIComponent(b.handle), acts = h('div', {'class': 'd-acts'});
  var act = function (text, fn, cls) {
    var el = h('button', {type: 'button', 'class': cls || 'quiet', text: text, disabled: ctx.canAct ? null : '', title: ctx.canAct ? null : 'Only a manager of this workspace, signed in on the board page'});
    el.addEventListener('click', fn); acts.appendChild(el); return el;
  };
  var done = function (close, text) { close(); ctx.said(text, true); ctx.again(); };
  act('role…', function () {
    var sel = h('select', {'aria-label': 'profile'}), fresh = h('input', {type: 'checkbox', id: 'dfresh'});
    api('GET', '/api/profiles').then(function (r) { r.profiles.forEach(function (x) { var o = h('option', {value: x.name, text: x.name}); if (x.name === (b.profile || '')) o.selected = true; sel.appendChild(o); }); });
    dialog('Role of @' + b.handle, [h('p', {'class': 'sub', text: 'The profile it has from its next prompt: its guide and skills change, and it keeps its name, token and channels.'}), h('div', {'class': 'form'}, h('label', null, 'Profile ', sel)),
      h('label', {'class': 'check', 'for': 'dfresh'}, fresh, ' Also give it a new session, so nothing of the old role stays in its context')],
      [{text: 'Change the role', run: function (el, close, fail) {
        el.disabled = true;
        api('PUT', url + '/profile', {profile: sel.value}).then(function () { return fresh.checked ? api('POST', url + '/reset') : null; })
          .then(function () { done(close, 'The role of @' + b.handle + ' is now ' + sel.value + (fresh.checked ? ', with a new session.' : '.')); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  });
  var sw = act('Takes new work', function () {
    var to = b.claims === 'mentioned' ? 'open' : 'mentioned';
    api('PUT', url + '/claims', {claims: to}).then(function () { ctx.said('@' + b.handle + (to === 'open' ? ' now takes new work.' : ' now works only when a thread mentions it.'), true); ctx.again(); }).catch(function (e) { ctx.said(e.message); });
  }, 'quiet switch');
  sw.setAttribute('role', 'switch'); sw.setAttribute('aria-checked', String(b.claims !== 'mentioned'));
  act('channels…', function () {
    var boxes = (w.channels || []).filter(function (c) { return !c.archived; }).map(function (c) {
      var i = h('input', {type: 'checkbox', value: c.slug}); if ((b.channels || []).indexOf(c.slug) >= 0) i.checked = true;
      return h('label', {'class': 'check'}, i, ' #' + c.slug);
    });
    dialog('Channels of @' + b.handle, [h('p', {'class': 'sub', text: 'The channels it claims new threads in. None ticked: any channel of the workspace.'})].concat(boxes),
      [{text: 'Save', run: function (el, close, fail) {
        el.disabled = true;
        var chosen = boxes.map(function (l) { return l.firstChild; }).filter(function (i) { return i.checked; }).map(function (i) { return i.value; });
        api('PUT', url + '/channels', {channels: chosen}).then(function () { done(close, 'Channels of @' + b.handle + ' saved.'); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  });
  act('voice…', function () {
    var v = h('textarea', {rows: '3', maxlength: '2000', 'aria-label': 'voice'}), p = h('textarea', {rows: '3', maxlength: '2000', 'aria-label': 'personality'});
    v.value = b.voice || ''; p.value = b.personality || '';
    dialog('Voice and personality of @' + b.handle, [h('label', null, 'Voice (how it writes to people)', v), h('label', null, 'Personality (who it is; expression only, the rules still win)', p),
      h('p', {'class': 'sub', text: 'Empty means the fleet default. It reaches the bot at its next wake; a long session may keep some of its old tone, and a new session starts clean.'})],
      [{text: 'Save', run: function (el, close, fail) {
        el.disabled = true;
        api('PUT', url + '/voice', {voice: v.value, personality: p.value}).then(function () { done(close, 'Voice and personality of @' + b.handle + ' saved.'); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  });
  act('new session…', function () {
    dialog('New session for @' + b.handle, [h('p', {text: 'Its harness session is set aside and it reads the rules again at its next run. It keeps its name, token and threads.'})],
      [{text: 'Give it a new session', run: function (el, close, fail) {
        el.disabled = true;
        api('POST', url + '/reset').then(function () { done(close, 'A new session is asked for @' + b.handle + '.'); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  });
  var others = (ctx.hosts || []).filter(function (x) { return x.name !== b._host; });
  if (b.pool && others.length) act('move…', function () {
    var sel = h('select', {'aria-label': 'machine'}, others.map(function (x) { return h('option', {value: x.name, text: x.name}); }));
    dialog('Move @' + b.handle, [h('p', {'class': 'sub', text: 'Same handle, token and cursor. Its keeper stops and packs it, and the keeper of the new machine unpacks and starts it.'}), h('div', {'class': 'form'}, h('label', null, 'To ', sel))],
      [{text: 'Move it', run: function (el, close, fail) {
        el.disabled = true;
        api('POST', url + '/move', {to: sel.value, start: false}).then(function () { done(close, 'Move asked for @' + b.handle + ' to ' + sel.value + '.'); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  });
  if (up) act('kill', function () {
    if (b.open_threads) { stopDialog(b, true, ctx); return; }
    dialog('Kill @' + b.handle + '?', [h('p', {text: 'It stops now: the run it is in is cut short and its reply is lost. Use stop to let the run finish.'})],
      [{text: 'Kill @' + b.handle, danger: true, run: function (el, close, fail) {
        el.disabled = true;
        api('PUT', url, {host: b._host, run: false, kill: true, harness: b.harness || null}).then(function () { done(close, 'Kill asked for @' + b.handle + '.'); }).catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
  }, 'quiet danger');
  d.appendChild(acts);
  if (b.host_flag) d.appendChild(hostBlock(b, ctx));
  return d;
}
// Whether a bot's launcher is alive: the board's word for it is `waiting` (idle) or `running` (a run in progress), as fleet.UP says.
// A bot that waits for work is up, so it offers stop and restart, not start.
function botUp(b) { return !!(b.state && b.state.up); }      // the board says what up means: fleet.bot_state
function botRows(bots, ctx) {
  return bots.map(function (b) {
    var st = b.state || {}, up = botUp(b);
    var tone = st.state === 'working' || st.state === 'waiting' ? 'ok' : up ? 'warn' : st.state === 'failing' || st.reason ? 'bad' : 'off';
    var go = h('button', {type: 'button', 'class': 'quiet', text: up ? 'stop' : 'start', disabled: ctx.canAct ? null : '', title: ctx.canAct ? null : 'Only a manager of this workspace, signed in on the board page'});
    go.addEventListener('click', function () { if (up && b.open_threads) ctx.stopThreads(b); else ctx.setRun(b, !up, go); });
    var again = h('button', {type: 'button', 'class': 'quiet', text: 'restart', 'aria-label': 'Restart @' + b.handle, hidden: up ? null : '', disabled: ctx.canAct ? null : ''});
    again.addEventListener('click', function () { ctx.restart(b, again); });
    var rm = h('button', {type: 'button', 'class': 'quiet danger', text: 'remove', 'aria-label': 'Remove @' + b.handle, disabled: ctx.canAct ? null : ''});
    rm.addEventListener('click', function () { ctx.remove(b, up); });
    // the drawer (#18527): what the board and the keeper know about the bot, and the changes a manager makes to it; built when first opened
    var dr = h('tr', {'class': 'drawer', id: 'drawer-' + b.handle, hidden: ''}, h('td', {colspan: '7'}));
    var more = h('button', {type: 'button', 'class': 'quiet', text: 'details ▾', 'aria-expanded': 'false', 'aria-controls': 'drawer-' + b.handle, 'aria-label': 'Details of @' + b.handle});
    var toggle = function (open) {
      dr.hidden = !open; more.setAttribute('aria-expanded', String(open)); more.textContent = open ? 'details ▴' : 'details ▾';
      if (open && !dr.firstChild.firstChild) dr.firstChild.appendChild(drawer(b, up, ctx));
      try {      // the page reloads after an action: this browser tab reopens the drawers that were open
        var list = JSON.parse(sessionStorage.getItem('bb.drawers') || '[]').filter(function (x) { return x !== b.handle; });
        sessionStorage.setItem('bb.drawers', JSON.stringify(open ? list.concat(b.handle) : list));
      } catch (e) {}
    };
    more.addEventListener('click', function () { toggle(dr.hidden); });
    try { if (JSON.parse(sessionStorage.getItem('bb.drawers') || '[]').indexOf(b.handle) >= 0) toggle(true); } catch (e) {}
    return [h('tr', null,
      h('td', null, h('span', {'class': 'dot ' + tone}), h('a', {href: '/a/' + encodeURIComponent(b.handle), 'class': 'mono', text: '@' + b.handle})),
      h('td', {'class': 'dim', text: st.words || b.status || ''}), h('td', {'class': 'mono', text: b.profile || ''}), h('td', {'class': 'mono', text: b._host}), h('td', {'class': 'mono', text: b.harness || ''}),
      h('td', {'class': 'mono', text: ago(b.last_post)}),
      h('td', {'class': 'acts'}, go, again, rm, more)), dr];
  }).reduce(function (all, pair) { return all.concat(pair); }, []);
}
function botsTable(bots, ctx) {
  return h('table', {'class': 'bots'}, h('thead', {'class': 'sr-only'}, h('tr', null, ['Bot', 'Doing', 'Role', 'Machine', 'Harness', 'Last active', 'Actions'].map(function (t) { return h('th', {scope: 'col', text: t}); }))),
    h('tbody', null, botRows(bots, ctx)));
}
function rolesView(w, roles, hosts) {
  var mine = [], reporting = hosts.filter(function (x) { return x.reporting; });
  hosts.forEach(function (hst) { hst.bots.forEach(function (b) { if (b.workspace === w.slug) { b._host = hst.name; mine.push(b); } }); });
  var sec = h('section', {'class': 'roles'}), out = h('p', {'class': 'sub', role: 'status'});
  var isUp = botUp;
  var said = function (text, ok) { note(out, text, ok); };
  var again = function () { setTimeout(function () { location.reload(); }, 2500); };
  var ctx = {
    w: w, hosts: reporting, allHosts: hosts, said: said, again: again,
    canAct: !!(ME && w.can_manage && !w.archived),
    stopThreads: function (b) { stopDialog(b, false, ctx); },
    setRun: function (b, run, el) {
      el.disabled = true;
      api('PUT', '/api/fleet/bots/' + encodeURIComponent(b.handle), {host: b._host, run: run, harness: b.harness || null})
        .then(function () { said((run ? 'Start' : 'Stop') + ' asked for @' + b.handle + '. The keeper on ' + b._host + ' does it within a few seconds' + (run ? '.' : '; a stop waits for the run the bot is in.'), true); again(); })
        .catch(function (e) { el.disabled = false; said(e.message); });
    },
    restart: function (b, el) {
      el.disabled = true;
      api('POST', '/api/fleet/bots/' + encodeURIComponent(b.handle) + '/restart')
        .then(function () { said('Restart asked for @' + b.handle + '. It starts again on the code on ' + b._host + ' once its run ends.', true); el.disabled = false; })
        .catch(function (e) { el.disabled = false; said(e.message); });
    },
    remove: function (b, up) {
      var role = roles.filter(function (r) { return (b.profile || '').toLowerCase() === r.profile.toLowerCase(); })[0];
      var others = role ? mine.filter(function (x) { return x !== b && isUp(x) && (x.profile || '').toLowerCase() === role.profile.toLowerCase(); }).length : 1;
      var nodes = [h('p', {text: 'Delete @' + b.handle + '? Its threads are released for pickup, its token stops working, and its machine removes its files. Its old messages stay.'})];
      if (role && up && !others) {
        nodes.push(h('p', {'class': 'warn', text: 'No bot would be running in the role ' + role.role + '. The agreement still lists it.' +
          (/architect|security/i.test(role.role) ? ' The agreement asks for this role\'s review on every pull request, so merges would wait for it.' : '')}));
      }
      dialog('Remove @' + b.handle, nodes, [{text: 'Delete @' + b.handle, danger: true, run: function (el, close, fail) {
        el.disabled = true;
        api('DELETE', '/api/agents/' + encodeURIComponent(b.handle)).then(function () { close(); said('@' + b.handle + ' is being removed.', true); again(); })
          .catch(function (e) { el.disabled = false; fail(e.message); });
      }}]);
    },
    add: function (role) {
      var inAgreement = function (name) { return roles.some(function (r) { return r.exists && r.profile.toLowerCase() === name.toLowerCase(); }); };
      var profile = role ? h('b', {text: role.profile}) : h('select', {'aria-label': 'profile'});
      var host = h('select', {'aria-label': 'machine'}, reporting.map(function (x) { return h('option', {value: x.name, text: x.name}); }));
      var harness = h('select', {'aria-label': 'harness'}, ['claude', 'codex', 'opencode', 'copilot'].map(function (x) { return h('option', {value: x, text: x}); }));
      var warn = h('p', {'class': 'warn', hidden: ''}), nodes = [];
      var fill = function () {
        var name = role ? role.profile : profile.value;
        warn.hidden = !name || inAgreement(name);
        warn.textContent = warn.hidden ? '' : 'The agreement does not mention the profile ' + name + '. The bot works, and it is listed under Outside the agreement. Changing the agreement to include it is a separate step.';
      };
      if (!role) api('GET', '/api/profiles').then(function (d) { d.profiles.forEach(function (x) { profile.appendChild(h('option', {value: x.name, text: x.name})); }); fill(); ready(); });
      profile.addEventListener && profile.addEventListener('change', fill);
      nodes.push(h('p', {'class': 'sub', text: role ? 'A bot for the role ' + role.role + '.' : 'A bot with any profile.'}));
      nodes.push(h('div', {'class': 'form'}, h('label', null, 'Profile ', profile), h('label', null, 'Machine ', host), h('label', null, 'Harness ', harness)));
      nodes.push(warn);
      nodes.push(h('p', {'class': 'sub', text: 'Whether the bot takes new work is set on the bot, from its profile, not in the agreement.'}));
      if (!reporting.length) { nodes.push(h('p', {'class': 'warn'}, 'No machine is reporting. ', h('a', {href: '/machines', text: 'Add one on the Machines page'}), ' first.')); }
      var start = null;
      var ready = function () { if (start) start.disabled = !(role ? role.profile : profile.value); };
      if (!role) profile.addEventListener('change', ready);
      dialog('Add a bot', nodes, reporting.length ? [{text: 'Start the bot', init: function (el) { start = el; ready(); }, run: function (el, close, fail) {
        var name = role ? role.profile : profile.value;
        el.disabled = true;
        api('POST', '/api/fleet/bots', {host: host.value, harness: harness.value, workspace: w.slug, profile: name})
          .then(function () { close(); said('Asked for a ' + name + ' bot. The keeper on ' + host.value + ' starts it within a few seconds.', true); again(); })
          .catch(function (e) { el.disabled = false; fail(e.message); });
      }}] : []);
    }
  };
  // Pause or resume the whole team (#654): one stop or start asked for every bot here. A paused team is one with bots and none running.
  var anyUp = mine.some(isUp), paused = mine.length > 0 && !anyUp;
  var pause = h('button', {type: 'button', 'class': 'quiet', text: paused ? 'Resume team' : 'Pause team', disabled: ctx.canAct && mine.length ? null : '', title: ctx.canAct ? null : 'Only a manager of this workspace, signed in on the board page'});
  pause.addEventListener('click', function () {
    var run = paused, todo = mine.filter(function (b) { return isUp(b) !== run; }), n = todo.length + ' bot' + (todo.length === 1 ? '' : 's');
    dialog((run ? 'Resume ' : 'Pause ') + n + '?', [h('p', {text: run ? 'This starts every bot of the workspace, including any you stopped by hand. The bots keep their threads.' : 'This stops every running bot of the workspace. A bot finishes the run it is in first, and reviews and merges wait until you resume.'})],
      [{text: (run ? 'Resume ' : 'Pause ') + n, run: function (el, close) {
        el.disabled = true; pause.disabled = true;
        Promise.allSettled(todo.map(function (b) { return api('PUT', '/api/fleet/bots/' + encodeURIComponent(b.handle), {host: b._host, run: run, harness: b.harness || null}); })).then(function (rs) {
          close();
          var bad = [];
          rs.forEach(function (r, k) { if (r.status === 'rejected') bad.push('@' + todo[k].handle + ': ' + r.reason.message); });
          said((run ? 'Resume' : 'Pause') + ' asked for ' + (todo.length - bad.length) + ' of ' + todo.length + '.' + (bad.length ? ' Failed: ' + bad.join('; ') : ' The keepers do it within a few seconds' + (run ? '; the bots keep their threads.' : '; a stop waits for the run a bot is in.')), !bad.length);
          again();
        });
      }}]);
  });
  var add = h('button', {type: 'button', text: '+ Add a bot', disabled: ctx.canAct ? null : '', title: ctx.canAct ? null : 'Only a manager of this workspace, signed in on the board page'});
  add.addEventListener('click', function () { ctx.add(null); });
  sec.appendChild(h('h2', null, 'Bots', h('small', {text: String(mine.length)}), pause, add));
  if (paused) sec.appendChild(h('p', {'class': 'warn', role: 'status', text: 'The team is paused: no bot is running. Resume team starts them again, and they keep their threads.'}));
  sec.appendChild(out);
  var seen = {};
  var group = function (title, profile, bots, note, role) {
    var run = bots.filter(isUp).length, stopped = bots.length - run;
    var status = run ? run + ' running' + (stopped ? ', ' + stopped + ' stopped' : '') : bots.length ? 'none running, ' + stopped + ' stopped' : 'no bot deployed';
    var head = h('div', {'class': 'rolehead'}, h('b', {text: title}), profile ? h('span', {'class': 'prof', text: 'profile ' + profile}) : null,
      h('span', {'class': 'status ' + (run ? 'good' : 'bad'), text: status}), note ? h('span', {'class': 'sub', text: note}) : null);
    if (role && role.exists && ctx.canAct) {
      var b = h('button', {type: 'button', 'class': 'quiet', text: '+ add', 'aria-label': 'Add a bot to ' + title}); b.addEventListener('click', function () { ctx.add(role); }); head.appendChild(b);
    }
    sec.appendChild(head);
    if (bots.length) sec.appendChild(botsTable(bots, ctx));
  };
  roles.forEach(function (r) {
    var bots = mine.filter(function (b) { return (b.profile || '').toLowerCase() === r.profile.toLowerCase(); });
    bots.forEach(function (b) { seen[b.handle] = 1; });
    var up = bots.filter(isUp).length;
    group(r.role, r.profile, bots, !r.exists ? 'this profile does not exist yet' : r.want > 1 || up > r.want ? 'expected ' + r.want + ', ' + up + ' running' : '', r);
  });
  var outside = mine.filter(function (b) { return !seen[b.handle]; });
  if (outside.length) group(roles.length ? 'Outside the agreement' : 'Bots', '', outside, roles.length ? 'profiles the agreement does not mention' : 'no agreement names roles yet');
  if (!mine.length && !roles.length) sec.appendChild(h('p', {'class': 'sub', text: 'No bot works here yet.'}));
  sec.appendChild(h('p', {'class': 'sub'}, 'A bot\'s details button opens its channels, voice, role and new session. Machine logins are on the ', h('a', {href: '/machines', text: 'Machines page'}), '.'));
  return sec;
}
// The board's resident bot. The board frees stuck threads by itself; the moderator is the judgment on
// top, and the one a person asks. A board with none running is told so (board thread 8378).
function moderatorNote(d) {
  var m = d.moderator || {};
  if (m.handle && m.running) return null;
  var what = m.handle ? '@' + m.handle + ', the moderator, is stopped. ' : 'No moderator: no bot in the admin workspace. ';
  return h('p', {'class': 'sub warn'}, what + 'The board frees threads whose worker is gone on its own; nobody is answering questions in the admin channel, finding reviewers, naming the next step for a stalled owner or freeing orphaned GitHub work. ',
    m.handle ? h('a', {href: '/workspaces/admin', text: 'Start it on the Admin workspace\'s Team tab'}) : h('a', {href: '/workspaces/admin', text: 'Add a bot in the admin workspace'}), '.');
}
function knowledgeNote(w) {
  return h('section', null, h('h2', {text: 'Knowledge'}),
    h('p', {'class': 'sub', text: w.seed_thread_id ? 'Seeding was requested: ' : 'Knowledge: ' + w.documents + ' document' + (w.documents === 1 ? '' : 's') + '.'},
      w.seed_thread_id ? h('a', {href: '/t/' + w.seed_thread_id, text: 'thread #' + w.seed_thread_id}) : null));
}
function channelsSection(w) {
  var sec = h('section', null, h('h2', null, 'Channels', h('small', {text: String(w.channels.length)})));
  var list = h('div', {'class': 'chs'});
  w.channels.forEach(function (c) {
    var row = h('div', {'class': 'ch'},
      h('div', {'class': 'n'}, h('a', {href: '/c/' + encodeURIComponent(c.slug), text: '#' + c.slug}), ' ', c.name && c.name !== c.slug ? c.name : '',
        c.home ? h('span', {'class': 'badge', text: 'General'}) : null, c.archived ? h('span', {'class': 'badge archived', text: 'archived'}) : null,
        c.seed_thread_id ? h('a', {'class': 'badge', href: '/t/' + c.seed_thread_id, text: 'seed thread'}) : null),
      c.topic ? h('div', {'class': 'tp', text: c.topic}) : null,
      c.focus ? h('div', {'class': 'fc', text: c.focus}) : null,
      c.references && c.references.length ? h('div', {'class': 'refs'}, 'reads: ', c.references.map(function (r, i) {
        var b = h('button', {type: 'button', 'class': 'link', text: r.path + (r.mandatory ? ' (mandatory)' : '')});
        b.addEventListener('click', function () { openDoc(r.path); });
        return [i ? ', ' : '', b];
      })) : null);
    if (w.can_manage && !w.archived) row.appendChild(editChannel(w, c));
    list.appendChild(row);
  });
  sec.appendChild(list);
  if (w.can_manage && !w.archived) sec.appendChild(newChannel(w));
  return sec;
}
function editChannel(w, c) {
  var out = h('p');
  var f = h('form', null, field('Name', 'name', 'text', c.name), field('Description', 'topic', 'textarea', c.topic),
    field('Focus: what a bot working a thread here should keep in mind', 'focus', 'textarea', c.focus),
    field('References into the knowledge base, one path per line; add "mandatory" to require it', 'references', 'textarea', refsText(c.references)),
    h('div', {'class': 'row'}, h('button', {type: 'submit', text: 'Save'}),
      c.home ? null : h('button', {type: 'button', 'class': 'quiet', text: c.archived ? 'Unarchive' : 'Archive', 'data-arch': '1'}),
      c.archived ? h('button', {type: 'button', 'class': 'quiet danger', text: 'Delete…', 'data-del': '1'}) : null), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    api('PATCH', '/api/workspaces/' + encodeURIComponent(w.slug) + '/channels/' + encodeURIComponent(c.slug),
        {name: f.elements.name.value.trim(), topic: f.elements.topic.value.trim(), focus: f.elements.focus.value.trim(),
         references: refsOf(f.elements.references.value)})
      .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
  });
  f.addEventListener('click', function (ev) {
    var path = '/api/workspaces/' + encodeURIComponent(w.slug) + '/channels/' + encodeURIComponent(c.slug);
    if (ev.target.closest('button[data-del]')) { deleteFlow(path, c.slug, '#' + c.slug, out, function () { location.reload(); }); return; }
    var b = ev.target.closest('button[data-arch]'); if (!b) return;
    api('PATCH', path, {archived: !c.archived})
      .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
  });
  return h('details', null, h('summary', {text: 'Edit'}), f);
}
function newChannel(w) {
  var out = h('p');
  var f = h('form', null,
    h('div', {'class': 'two'}, field('Slug (becomes #' + w.slug + '.slug)', 'slug', 'text', '', {required: '', pattern: '[A-Za-z0-9][A-Za-z0-9._-]{1,63}'}),
      field('Name', 'name', 'text', '', {required: ''})),
    field('Description', 'description', 'textarea'),
    field('Focus: what a bot working a thread here should keep in mind', 'focus', 'textarea'),
    field('References into the knowledge base, one path per line; add "mandatory" to require it', 'references', 'textarea'),
    seedFields(), h('button', {type: 'submit', text: 'Create channel'}), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var body = {slug: f.elements.slug.value.trim(), name: f.elements.name.value.trim(), description: f.elements.description.value.trim(),
                focus: f.elements.focus.value.trim(), references: refsOf(f.elements.references.value)};
    var seed = seedOf(f); if (seed) body.seed = seed;
    api('POST', '/api/workspaces/' + encodeURIComponent(w.slug) + '/channels', body)
      .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
  });
  return h('details', null, h('summary', {text: '+ New channel'}), f);
}
// ---- the knowledge base: a folder tree beside a reader --------------------------
// Markdown is rendered from escaped text, so nothing a bot writes becomes markup.
function esc(t) { return String(t).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
function inline(t) {
  return esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|[A-Za-z0-9_.\/-]+\.md)\)/g, function (m, label, href) {
      return /^https?:/.test(href) ? '<a href="' + href + '" target="_blank" rel="noopener">' + label + '</a>'
                                    : '<a href="#doc=' + href + '" data-doc="' + href + '">' + label + '</a>';
    })
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}
function markdown(src) {
  var out = [], lines = String(src).replace(/\r\n/g, '\n').split('\n'), i = 0, list = null, para = [];
  function flush() {
    if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; }
    if (list) { out.push('</' + list + '>'); list = null; }
  }
  while (i < lines.length) {
    var l = lines[i], m;
    if (/^```/.test(l)) {
      flush(); var code = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>'); i++; continue;
    }
    if ((m = /^(#{1,4})\s+(.*)$/.exec(l))) { flush(); out.push('<h' + (m[1].length + 2) + '>' + inline(m[2]) + '</h' + (m[1].length + 2) + '>'); }
    else if ((m = /^\s*([-*]|\d+[.)])\s+(.*)$/.exec(l))) {
      var kind = /\d/.test(m[1]) ? 'ol' : 'ul';
      if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; }
      if (list !== kind) { if (list) out.push('</' + list + '>'); out.push('<' + kind + '>'); list = kind; }
      out.push('<li>' + inline(m[2]) + '</li>');
    }
    else if (/^\s*>\s?/.test(l)) { flush(); out.push('<blockquote>' + inline(l.replace(/^\s*>\s?/, '')) + '</blockquote>'); }
    else if (/^\s*(---|\*\*\*)\s*$/.test(l)) { flush(); out.push('<hr>'); }
    else if (!l.trim()) flush();
    else { if (list) { out.push('</' + list + '>'); list = null; } para.push(l); }
    i++;
  }
  flush();
  return out.join('');
}
var openDoc = function () {};    // set by knowledgeSection; channel references call it
function knowledgeSection(w) {
  var base = '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge';
  var sec = h('section', {id: 'kb'}, h('h2', null, 'Knowledge base', h('small', {text: String(w.documents) + ' document' + (w.documents === 1 ? '' : 's')})));
  sec.appendChild(h('p', {'class': 'sub', text: "The workspace's shared memory: revisioned documents its bots read and write. INDEX.md is where a bot starts; a channel's folder holds what that channel worked out."}));
  var search = h('input', {type: 'search', placeholder: 'search the documents…', 'aria-label': 'Search the documents'});
  var tree = h('nav', {'class': 'kb-tree', 'aria-label': 'Documents'});
  var reader = h('article', {'class': 'kb-reader'}, h('p', {'class': 'sub', text: 'Pick a document.'}));
  var back = h('button', {type: 'button', 'class': 'kb-back', text: '‹ Knowledge'});
  var kbBox = h('div', {'class': 'kb'}, h('div', {'class': 'kb-side'}, search, tree), back, reader);
  back.addEventListener('click', function () { kbBox.classList.remove('reading'); });      // on a phone the tree and the reader are two screens
  sec.appendChild(kbBox);
  var docs = [], current = null;
  var enc = function (p) { return p.split('/').map(encodeURIComponent).join('/'); };
  function drawTree(list, q) {
    tree.replaceChildren();
    if (!list.length) { tree.appendChild(h('p', {'class': 'sub', text: q ? 'No document matches.' : 'Nothing written yet.'})); return; }
    var folders = {}, order = [];
    list.forEach(function (d) {
      var k = d.path.indexOf('/') > 0 ? d.path.slice(0, d.path.lastIndexOf('/')) : '';
      if (!folders[k]) { folders[k] = []; order.push(k); }
      folders[k].push(d);
    });
    order.sort(function (a, b) { return a === '' ? -1 : b === '' ? 1 : a.localeCompare(b); });
    order.forEach(function (k) {
      var items = folders[k].sort(function (a, b) {
        var ai = /(^|\/)INDEX\.md$/.test(a.path), bi = /(^|\/)INDEX\.md$/.test(b.path);
        return ai !== bi ? (ai ? -1 : 1) : a.path.localeCompare(b.path);
      });
      var group = h('div', {'class': 'kb-folder'}, k ? h('div', {'class': 'kb-fname', text: k + '/'}) : null);
      items.forEach(function (d) {
        var b = h('button', {type: 'button', 'class': 'kb-item' + (d.path === current ? ' on' : ''), 'data-path': d.path,
                             title: d.path + ' · revision ' + d.revision + ' · ' + d.bytes + ' bytes'},
                  d.path.slice(k ? k.length + 1 : 0));
        b.addEventListener('click', function () { show(d.path); });
        group.appendChild(b);
      });
      tree.appendChild(group);
    });
  }
  function show(path, rev) {
    current = path; kbBox.classList.add('reading');
    Array.prototype.forEach.call(tree.querySelectorAll('.kb-item'), function (b) { b.classList.toggle('on', b.dataset.path === path); });
    history.replaceState(null, '', '#doc=' + path + (rev ? '@' + rev : ''));
    reader.replaceChildren(h('p', {'class': 'sub', text: 'Loading ' + path + '…'}));
    var url = base + '/' + enc(path) + (rev ? '?revision=' + rev : '');
    Promise.all([api('GET', url), api('GET', base + '/' + enc(path) + '/history').catch(function () { return null; })]).then(function (rs) {
      var d = rs[0].document, revs = (rs[1] && (rs[1].revisions || rs[1].history)) || [];
      var meta = h('div', {'class': 'kb-meta'},
        h('span', {'class': 'kb-path', text: d.path}),
        h('span', {text: 'revision ' + d.revision + (rev ? ' (older)' : '')}),
        h('span', {text: (d.created_at || '').replace('T', ' ').slice(0, 16)}),
        d.author ? h('a', {href: '/a/' + encodeURIComponent(d.author), text: '@' + d.author}) : null);
      var body = h('div', {'class': 'md'}); body.innerHTML = markdown(d.body);
      var foot = h('div', {'class': 'kb-foot'});
      if (d.reason) foot.appendChild(h('p', {'class': 'sub', text: 'Why this revision: ' + d.reason}));
      if (d.sources && d.sources.length) foot.appendChild(h('p', {'class': 'sub'}, 'Sources: ', d.sources.map(function (s, i) {
        var link = /^\d+$/.test(String(s)) ? h('a', {href: '/t/' + s, text: 'message ' + s})
                 : /^https?:/.test(String(s)) ? h('a', {href: s, target: '_blank', rel: 'noopener', text: s}) : String(s);
        return [i ? ', ' : '', link];
      })));
      if (revs.length > 1) {
        var hl = h('div', {'class': 'kb-hist'}, h('span', {'class': 'sub', text: 'History: '}));
        revs.forEach(function (r) {
          var b = h('button', {type: 'button', 'class': 'link' + (r.revision === d.revision ? ' on' : ''),
                               title: (r.reason || '') + (r.author ? ' · @' + r.author : ''), text: 'r' + r.revision});
          b.addEventListener('click', function () { show(path, r.revision === revs[0].revision ? null : r.revision); });
          hl.appendChild(b);
        });
        foot.appendChild(hl);
      }
      reader.replaceChildren(meta, body, foot);
    }).catch(function (e) { reader.replaceChildren(h('p', {'class': 'msg-err', text: e.message})); });
  }
  openDoc = function (path) { sec.scrollIntoView({behavior: 'smooth'}); show(path); };
  reader.addEventListener('click', function (ev) {
    var a = ev.target.closest('a[data-doc]'); if (!a) return;
    ev.preventDefault();
    var target = a.dataset.doc, dir = current && current.indexOf('/') > 0 ? current.slice(0, current.lastIndexOf('/') + 1) : '';
    var resolve = function (p) {      // a link is relative to the document holding it
      var out = [];
      p.split('/').forEach(function (seg) { if (seg === '..') out.pop(); else if (seg && seg !== '.') out.push(seg); });
      return out.join('/');
    };
    var exists = function (p) { return docs.some(function (d) { return d.path === p; }); };
    var rel = resolve(dir + target);
    show(exists(rel) || !exists(resolve(target)) ? rel : resolve(target));
  });
  var timer = null;
  search.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = search.value.trim();
      if (!q) { drawTree(docs, ''); return; }
      api('GET', base + '?q=' + encodeURIComponent(q)).then(function (d) { drawTree(d.documents, q); })
        .catch(function (e) { tree.replaceChildren(h('p', {'class': 'msg-err', text: e.message})); });
    }, 250);
  });
  window.addEventListener('hashchange', function () {     // a pasted or edited #doc= link
    var m = /#doc=([^@]+)(?:@(\d+))?$/.exec(decodeURIComponent(location.hash));
    if (m) show(m[1], m[2] ? Number(m[2]) : null);
  });
  api('GET', base).then(function (d) {
    docs = d.documents; drawTree(docs, '');
    var m = /#doc=([^@]+)(?:@(\d+))?$/.exec(decodeURIComponent(location.hash));
    if (m) show(m[1], m[2] ? Number(m[2]) : null);
    else if (docs.some(function (x) { return x.path === 'INDEX.md'; })) show('INDEX.md');
  }).catch(function (e) { tree.replaceChildren(h('p', {'class': 'msg-err', text: e.message})); });
  return sec;
}
function settingsSection(w) {
  var out = h('p');
  var f = h('form', null, field('Name', 'name', 'text', w.name),
    field('Purpose', 'purpose', 'textarea', w.purpose),
    h('div', {'class': 'row'}, h('button', {type: 'submit', text: 'Save'}), h('button', {type: 'button', 'class': 'quiet', text: 'Archive workspace', 'data-arch': '1'})), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    api('PATCH', '/api/workspaces/' + encodeURIComponent(w.slug), {name: f.elements.name.value.trim(), purpose: f.elements.purpose.value.trim()})
      .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
  });
  f.addEventListener('click', function (ev) {
    var b = ev.target.closest('button[data-arch]'); if (!b) return;
    if (!confirm('Archive ' + w.slug + '? Its channels become read-only and its bots are released.')) return;
    api('PATCH', '/api/workspaces/' + encodeURIComponent(w.slug), {archived: true, archive_confirm: true})
      .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
  });
  return h('section', null, h('h2', {text: 'Settings'}), f);
}

// Deleting (#473): only what is archived, after a warning that counts what goes, with the slug typed back.
function deleteFlow(path, slug, label, out, done) {
  api('GET', path + '/deletion').then(function (d) {
    var g = d.deletion, n = function (k, one) { return g[k] + ' ' + one + (g[k] === 1 ? '' : 's'); };
    if (g.bound_only && g.bound_only.length) {
      note(out, 'These bots are bound to this channel only. Bind them to another channel in the bot\'s details on the Team tab, or delete them, first: ' + g.bound_only.map(function (b) { return '@' + b; }).join(', ') + '.');
      return;
    }
    if (g.bots && g.bots.length) {
      note(out, 'Move or delete its bots first: ' + g.bots.map(function (b) { return '@' + b; }).join(', ') + '. Change them on the Team tab.');
      return;
    }
    var what = [g.channels.length > 1 ? n('channels', 'channel') : null, n('threads', 'thread'), n('messages', 'message'),
                n('attachments', 'attachment'), g.documents !== undefined ? n('documents', 'knowledge document') : null]
      .filter(Boolean).join(', ');
    var typed = prompt('Delete ' + label + ' for good?\n\nThis removes ' + what + '.' +
      (g.routines.length ? ' Routines that post there are deleted too: ' + g.routines.join(', ') + '.' : '') +
      ' It cannot be undone.\n\nType ' + slug + ' to confirm.');
    if (typed === null) return;
    if (typed.trim() !== slug) { note(out, 'Not deleted: the slug you typed does not match.'); return; }
    api('DELETE', path, {confirm: slug}).then(done).catch(function (e) { note(out, e.message); });
  }).catch(function (e) { note(out, e.message); });
}
function deleteSection(w) {
  var out = h('p'), b = h('button', {type: 'button', 'class': 'quiet danger', text: 'Delete this workspace…'});
  b.addEventListener('click', function () {
    deleteFlow('/api/workspaces/' + encodeURIComponent(w.slug), w.slug, 'the workspace ' + w.slug, out,
               function () { location.href = '/workspaces'; });
  });
  return h('section', null, h('h2', {text: 'Delete'}),
    h('p', {'class': 'sub', text: 'An archived workspace can be deleted with everything in it: its channels, their messages and attachments, the routines that post there and its knowledge base. Its bots must be moved or deleted first.'}),
    b, out);
}

if (SLUG) wsView(); else listView();
})();
"""


def workspaces_page(name: str) -> str:
    """The whole page for /workspaces and /workspaces/{slug}; the script fills it in."""
    return shell(name, "workspaces", here="/workspaces", content=
                 '<style>' + WS_CSS + '</style>'
                 '<div class="ws" id="ws"><p class="sub">Loading workspaces…</p>'
                 '<noscript><p class="sub">This page needs JavaScript. The same data is at '
                 '<a href="/api/workspaces">/api/workspaces</a>.</p></noscript></div>'
                 '<script>' + WS_SWITCHER_JS + WS_JS + '</script>')
