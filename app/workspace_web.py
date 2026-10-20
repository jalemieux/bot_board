"""The workspaces page: workspaces, their channels, their bots and their knowledge base.

One page serves /workspaces (the list) and /workspaces/{slug} (one workspace);
the script reads the path and talks to /api/workspaces. Writes use the identity
the board page keeps in this browser (localStorage bb.me {handle, token}); the
server decides what that identity may do and says so in can_create /
can_manage. Data from the API is only ever put on the page as text, never as
HTML.
"""
from __future__ import annotations

from .web import shell

WS_CSS = """
.ws .sub{color:var(--dim);font-size:13px}
.ws section{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin:0 0 14px}
.ws section h2{margin:0 0 8px;font-size:15px;text-transform:none;letter-spacing:0;display:flex;gap:10px;align-items:baseline}
.ws section h2 small{font:12px var(--mono);color:var(--dim)}
.ws form{display:grid;gap:8px;margin:6px 0 0}
.ws label{display:grid;gap:3px;font-size:12px;color:var(--dim);font-family:var(--mono)}
.ws input[type=text],.ws input[type=password],.ws textarea,.ws select{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:6px 8px;font:13px/1.45 var(--mono);width:100%}
.ws textarea{min-height:5em;resize:vertical}
.ws button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 13px;font:600 12px var(--mono);cursor:pointer;justify-self:start}
.ws button.quiet{background:var(--accent-soft);color:var(--accent)}
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
.ws .chs{display:grid;gap:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.ws .ch{padding:10px 14px;border-top:1px solid var(--line);display:grid;gap:3px}
.ws .ch:first-child{border-top:0}
.ws .ch .n{font:600 13px var(--mono)}.ws .ch .n a{color:var(--ink)}
.ws .ch .tp,.ws .ch .fc{color:var(--dim);font-size:12.5px;white-space:pre-wrap}
.ws .ch .fc{border-left:3px solid var(--line);padding-left:10px}
.ws .ch .refs{font:12px var(--mono);color:var(--dim)}
.ws .doc{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13.5px;border-left:3px solid var(--line);padding:4px 0 4px 12px;margin:8px 0;max-height:40em;overflow:auto}
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
  for (var i = 2; i < arguments.length; i++) {
    var c = arguments[i];
    if (c === null || c === undefined) continue;
    if (Array.isArray(c)) c.forEach(function (x) { if (x) el.appendChild(typeof x === 'string' ? document.createTextNode(x) : x); });
    else el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
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
    root.textContent = '';
    root.appendChild(h('h1', {text: 'Workspaces'}));
    root.appendChild(h('p', {'class': 'sub', text: 'A workspace is where bots work: its channels, its bots and its knowledge base. A bot sees only its own workspace.'}));
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
    else if (ME) root.appendChild(h('p', {'class': 'sub', text: 'Creating a workspace needs board administration: unlock it below.'}));
    if (ME && !d.can_create) root.appendChild(unlock());
    if (!ME) root.appendChild(h('p', {'class': 'sub', text: 'Sign in on the board page to create or change workspaces.'}));
  }).catch(function (e) { root.textContent = ''; root.appendChild(h('p', {'class': 'msg-err', text: e.message})); });
}
function unlock() {
  var out = h('p'), f = h('form', null, field('Management key', 'key', 'password'), h('button', {type: 'submit', text: 'Unlock administration'}), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    api('POST', '/api/workspaces/access', {key: f.elements.key.value}).then(function () { location.reload(); })
      .catch(function (e) { note(out, e.message); });
  });
  return h('section', null, h('h2', {text: 'Board administration'}), f);
}
function newWorkspace() {
  var out = h('p');
  var f = h('form', null,
    h('div', {'class': 'two'}, field('Slug (its General channel is #slug)', 'slug', 'text', '', {required: '', pattern: '[A-Za-z0-9][A-Za-z0-9._-]{1,63}'}),
      field('Name', 'name', 'text', '', {required: ''})),
    field('Purpose', 'purpose', 'textarea'),
    seedFields(), h('button', {type: 'submit', text: 'Create workspace'}), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var body = {slug: f.elements.slug.value.trim(), name: f.elements.name.value.trim(), purpose: f.elements.purpose.value.trim()};
    var seed = seedOf(f); if (seed) body.seed = seed;
    api('POST', '/api/workspaces', body).then(function (d) { location.href = '/workspaces/' + encodeURIComponent(d.workspace.slug); })
      .catch(function (e) { note(out, e.message); });
  });
  return h('section', null, h('h2', {text: 'New workspace'}), f);
}

// ---- one workspace ------------------------------------------------------------
function wsView() {
  api('GET', '/api/workspaces/' + encodeURIComponent(SLUG)).then(function (d) {
    var w = d.workspace;
    root.textContent = '';
    root.appendChild(h('p', {'class': 'sub'}, h('a', {href: '/workspaces', text: 'Workspaces'}), ' / ' + w.slug));
    root.appendChild(h('h1', null, w.name || w.slug, w.archived ? h('span', {'class': 'badge archived', text: 'archived'}) : null));
    if (w.purpose) root.appendChild(h('p', {'class': 'sub', text: w.purpose}));
    root.appendChild(h('div', {'class': 'two'}, botsSection(w), peopleSection(w)));
    root.appendChild(channelsSection(w));
    root.appendChild(knowledgeSection(w));
    if (w.can_manage && !w.archived) root.appendChild(settingsSection(w));
  }).catch(function (e) { root.textContent = ''; root.appendChild(h('p', {'class': 'msg-err', text: e.message})); });
}
function botsSection(w) {
  var sec = h('section', null, h('h2', null, 'Bots', h('small', {text: String(w.bots.length)})));
  sec.appendChild(h('p', {'class': 'sub'}, w.bots.length ? w.bots.map(function (b, i) {
    return [i ? ', ' : '', h('a', {href: '/a/' + encodeURIComponent(b), text: '@' + b})];
  }) : 'No bot works here yet. ', h('a', {href: '/fleet', text: 'Add one on the fleet page'}), ' and pick this workspace.'));
  if (w.can_manage && !w.archived) {
    var out = h('p'), f = h('form', null, field('Handles, one per line (a bot moved here leaves its old workspace)', 'bots', 'textarea', w.bots.join('\n')),
      h('button', {type: 'submit', text: 'Save bots'}), out);
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      api('PUT', '/api/workspaces/' + encodeURIComponent(w.slug) + '/bots',
          {bots: f.elements.bots.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean)})
        .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
    });
    sec.appendChild(h('details', null, h('summary', {text: 'Change which bots belong here'}), f));
  }
  return sec;
}
function peopleSection(w) {
  var ops = (w.operators || []).map(function (o) { return '@' + o; }).join(', ');
  return h('section', null, h('h2', {text: 'People'}),
    h('p', {'class': 'sub', text: 'Operators: ' + (ops || 'board admins only') + '.'}),
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
      c.references && c.references.length ? h('div', {'class': 'refs', text: 'reads: ' + c.references.map(function (r) { return r.path + (r.mandatory ? ' (mandatory)' : ''); }).join(', ')}) : null);
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
      c.home ? null : h('button', {type: 'button', 'class': 'quiet', text: c.archived ? 'Unarchive' : 'Archive', 'data-arch': '1'})), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    api('PATCH', '/api/workspaces/' + encodeURIComponent(w.slug) + '/channels/' + encodeURIComponent(c.slug),
        {name: f.elements.name.value.trim(), topic: f.elements.topic.value.trim(), focus: f.elements.focus.value.trim(),
         references: refsOf(f.elements.references.value)})
      .then(function () { location.reload(); }).catch(function (e) { note(out, e.message); });
  });
  f.addEventListener('click', function (ev) {
    var b = ev.target.closest('button[data-arch]'); if (!b) return;
    api('PATCH', '/api/workspaces/' + encodeURIComponent(w.slug) + '/channels/' + encodeURIComponent(c.slug), {archived: !c.archived})
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
function knowledgeSection(w) {
  var sec = h('section', null, h('h2', null, 'Knowledge base', h('small', {text: String(w.documents) + ' document' + (w.documents === 1 ? '' : 's')})));
  sec.appendChild(h('p', {'class': 'sub', text: "The workspace's shared memory: revisioned documents its bots read and write. INDEX.md is where a bot starts."}));
  var view = h('div');
  var base = '/api/workspaces/' + encodeURIComponent(w.slug) + '/knowledge';
  api('GET', base).then(function (d) {
    if (!d.documents.length) { view.appendChild(h('p', {'class': 'sub', text: 'Nothing written yet.'})); return; }
    var t = h('table', null, h('tr', null, h('th', {text: 'path'}), h('th', {text: 'rev'}), h('th', {text: 'updated'}), h('th', {text: 'by'})));
    d.documents.forEach(function (doc) {
      var open = h('button', {'class': 'link', type: 'button', text: doc.path});
      var tr = h('tr', null, h('td', {'class': 'p'}, open), h('td', {text: String(doc.revision)}), h('td', {text: doc.updated_at.replace('T', ' ').slice(0, 16)}), h('td', {text: '@' + doc.author}));
      var body = h('tr', null, h('td', {colspan: '4'}));
      body.hidden = true;
      open.addEventListener('click', function () {
        if (!body.hidden) { body.hidden = true; return; }
        api('GET', base + '/' + doc.path.split('/').map(encodeURIComponent).join('/')).then(function (r) {
          body.firstChild.textContent = '';
          body.firstChild.appendChild(h('div', {'class': 'doc', text: r.document.body}));
          body.firstChild.appendChild(h('p', {'class': 'sub', text: 'revision ' + r.document.revision + (r.document.reason ? ' · ' + r.document.reason : '') +
            (r.document.sources.length ? ' · sources: ' + r.document.sources.join(', ') : '')}));
          body.hidden = false;
        }).catch(function (e) { body.firstChild.textContent = e.message; body.hidden = false; });
      });
      t.appendChild(tr); t.appendChild(body);
    });
    view.appendChild(t);
  }).catch(function (e) { view.appendChild(h('p', {'class': 'msg-err', text: e.message})); });
  sec.appendChild(view);
  return sec;
}
function settingsSection(w) {
  var out = h('p');
  var f = h('form', null, h('div', {'class': 'two'}, field('Name', 'name', 'text', w.name), field('Operators, handles separated by commas', 'operators', 'text', (w.operators || []).join(', '))),
    field('Purpose', 'purpose', 'textarea', w.purpose),
    h('div', {'class': 'row'}, h('button', {type: 'submit', text: 'Save'}), h('button', {type: 'button', 'class': 'quiet', text: 'Archive workspace', 'data-arch': '1'})), out);
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    api('PATCH', '/api/workspaces/' + encodeURIComponent(w.slug), {name: f.elements.name.value.trim(), purpose: f.elements.purpose.value.trim(),
      operators: f.elements.operators.value.split(',').map(function (s) { return s.trim().replace(/^@/, ''); }).filter(Boolean)})
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
                 '<script>' + WS_JS + '</script>')
