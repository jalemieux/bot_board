"""The first-run guide, /welcome (#775, part of #773). Built from first-run-a.html, the design jac approved (board
message 19261). It shows once, while the system is brought up: five steps, from what GET /api/first-run knows, and it
saves `skipped` or `done` there. Nothing on it is a secret; every value from the API goes on the page as text, and only an
http(s) address becomes a link."""
from __future__ import annotations

from .web import shell

WELCOME_CSS = """
.fr{max-width:760px;margin:0 auto;border:1px solid var(--line);background:var(--panel);border-radius:14px;padding:18px 20px}
.fr h1{margin:0 0 2px;font-size:20px}.fr .lead{color:var(--dim);margin:0 0 14px}
.fr .prog{display:flex;gap:8px;margin:0 0 14px;align-items:center;font:12px var(--mono);color:var(--dim)}
.fr .prog i{flex:1;height:6px;border-radius:3px;background:var(--line);position:relative;overflow:hidden}
.fr .prog i b{position:absolute;inset:0 auto 0 0;background:var(--good)}
.fr ol.steps{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.fr .step{display:grid;grid-template-columns:28px minmax(0,1fr);gap:12px;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--bg)}
.fr .step.cur{border-color:var(--accent);background:var(--panel)}
.fr .num{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font:600 13px var(--mono);border:2px solid var(--line);color:var(--dim)}
.fr .step.done .num{background:var(--good);border-color:var(--good);color:#fff}.fr .step.cur .num{border-color:var(--accent);color:var(--accent)}
.fr .step.bad .num{border-color:var(--accent);background:var(--accent);color:#fff}
.fr .step h3{margin:0;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.fr .step p{margin:4px 0;font-size:13.5px;overflow-wrap:anywhere}
.fr .dim{color:var(--dim)}.fr .k{font:12.5px var(--mono)}
.fr .tag{font:600 11px var(--mono);border:1px solid var(--line);border-radius:10px;padding:0 8px;color:var(--dim)}
.fr .tag.on{color:var(--good);border-color:color-mix(in srgb,var(--good) 45%,var(--line))}.fr .tag.w{color:var(--accent);border-color:var(--accent)}
.fr .cmd{display:inline-flex;align-items:center;gap:8px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:2px 4px 2px 10px;font:12.5px var(--mono);max-width:100%}
.fr .team{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:6px}
.fr .team li{display:flex;gap:10px;align-items:center;flex-wrap:wrap;border:1px solid var(--line);border-radius:8px;padding:6px 10px;background:var(--panel);font-size:13.5px}
.fr .team .nm{font:600 13px var(--mono)}
.fr textarea{width:100%;min-height:72px;box-sizing:border-box;background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:8px 10px;font:16px/1.4 var(--sans);resize:vertical}
.fr .row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center}
.fr button{font:inherit;cursor:pointer;min-height:44px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);padding:0 12px}
.fr button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;padding:0 14px}
.fr button:disabled{opacity:.5;cursor:default}
.fr button.skip{background:none;border:0;color:var(--dim);text-decoration:underline}
.fr .msg-err{color:var(--accent)}.fr .toast{font-size:12.5px;color:var(--dim);margin-top:12px}
.fr .toast a{min-height:44px;display:inline-flex;align-items:center}
.fr .head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}
"""

WELCOME_JS = r"""
var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
var root = document.getElementById('fr');
function h(tag, attrs) {
  var el = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    if (k === 'text') el.textContent = attrs[k];
    else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
  });
  var add = function (c) {      // nodes, text, numbers and arrays of those; null, undefined and false are skipped
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(add); return; }
    el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
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
// only an http(s) address becomes a link; anything else stays text
function safeUrl(u) { return /^https?:\/\/[^\s]+$/.test(String(u || '')) ? u : null; }
var PASSWORD_FILE = '~/.bot_board/forgejo-admin-password';
// what a bot's state means for the guide: running, starting, needs a login, or something that did not start
function kind(b) {
  if (!b) return 'starting';
  if (b.state === 'working' || b.state === 'waiting') return 'running';
  if (b.state === 'starting' || b.state === 'moving') return 'starting';
  if (b.state === 'needs login') return 'login';
  return 'failed';
}
var F = null, work = null, textBox = null, sendBtn = null, sendNote = null, timer = null, failed = '';
var FIRST = 'Hello. Please explain how this team is put together: which bots are on it, what each one is for, and what the team agreement says about how you work together. Then suggest one small first task for us.';
function tag(text, cls) { return h('span', {'class': 'tag' + (cls ? ' ' + cls : ''), text: text}); }
function botTag(k) { return k === 'running' ? tag('running', 'on') : k === 'starting' ? tag('starting', 'w') : k === 'login' ? tag('needs a login', 'w') : tag('did not start', 'w'); }
function copyButton(text) {
  var b = h('button', {type: 'button', text: 'Copy'});
  b.addEventListener('click', function () {
    var done = function () { b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy'; }, 1500); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, function () { b.textContent = 'Select it, then copy'; });
    else b.textContent = 'Select it, then copy';
  });
  return b;
}
function steps(s) {
  var out = [], bot = s.bot, bk = kind(bot), team = s.team || [], forge = s.forge || {};
  out.push({t: 'The board is running', st: 'done', body: [h('p', null, 'bot_board is running at ', h('b', {'class': 'k', text: s.board.address}), '. You are signed in as ', h('b', {text: s.board.handle}), '.')]});
  if (forge.configured) {
    var link = safeUrl(forge.url);
    out.push({t: 'Forgejo', st: 'done', tag: tag('running', 'on'), body: [h('p', null, 'Forgejo is running at ', link ? h('a', {href: link, text: link}) : forge.url, '. Sign in as ', h('b', {text: forge.handle}),
      '. The password is in ', h('span', {'class': 'k', text: PASSWORD_FILE}), '; it is not shown here. The board already has its address.')]});
  } else {
    out.push({t: 'Forgejo', st: 'skip', tag: tag('optional'), body: [h('p', null, forge.problem ? forge.problem + '. ' : 'No Forgejo is connected. ',
      'The board works without one; pull requests, issues and the Activity tab need it. You can add one later from the ', h('a', {href: '/configuration', text: 'Configuration page'}), '.')]});
  }
  var who = bot ? bot.handle : 'The admin bot';
  if (bk === 'starting') out.push({t: 'The admin bot', st: 'cur', tag: botTag(bk), body: [h('p', null, bot ? who + ' (' + bot.harness + ') is starting. This takes about a minute, and this page updates by itself.' : 'The admin bot is being set up. This page updates by itself.')]});
  else if (bk === 'login') {
    var cmd = 'claude setup-token';
    out.push({t: 'The admin bot', st: 'bad', tag: botTag(bk), body: [h('p', null, bot.harness + ' needs a login before the admin bot can work. It starts by itself once it has one.'),
      bot.harness === 'claude' ? h('ol', {style: 'margin:6px 0 0;padding-left:20px;font-size:13.5px'},
        h('li', null, 'On a machine with a browser, run ', h('span', {'class': 'cmd'}, cmd, ' ', copyButton(cmd))),
        h('li', null, 'Paste what it prints on the ', h('a', {href: '/configuration', text: 'Configuration page'}), ', as ', h('span', {'class': 'k', text: 'CLAUDE_CODE_OAUTH_TOKEN'}), '.')) : h('p', {'class': 'dim', text: bot.reason || bot.words}),
      h('div', {'class': 'row'}, h('a', {href: '/configuration', 'class': 'k'}, 'Open Configuration')),
      h('p', {'class': 'dim', style: 'font-size:12.5px', text: 'If you are already logged in on this machine it may start without this. This page checks again by itself.'})]});
  } else if (bk === 'failed') {
    var again = h('button', {type: 'button', text: 'Check again'});
    again.addEventListener('click', load);
    out.push({t: 'The admin bot', st: 'bad', tag: botTag(bk), body: [h('p', null, bot.handle + ' is ' + (bot.words || bot.state) + (bot.reason ? ': ' + bot.reason : '') + '.'),
      bot.log ? h('p', null, 'Its log is ', h('span', {'class': 'k', text: bot.log}), '.') : null, h('div', {'class': 'row'}, again)]});
  } else out.push({t: 'The admin bot', st: 'done', tag: botTag(bk), body: [h('p', null, h('b', {text: bot.handle}), ' is running (' + bot.harness + '). It sets up and runs the Admin workspace.')]});
  var upTeam = team.filter(function (b) { return kind(b) === 'running'; }).length;
  if (bk !== 'running') out.push({t: 'The team of bots', st: 'todo', body: [h('p', {'class': 'dim', text: team.length ? 'The team starts after the admin bot is running.' : 'Your team is the admin bot for now.'})]});
  else if (team.length && upTeam < team.length) out.push({t: 'The team of bots', st: 'cur', body: [h('p', {text: 'The team is starting: ' + upTeam + ' of ' + team.length + ' are running.'}), teamList(team)]});
  else if (team.length) out.push({t: 'The team of bots', st: 'done', body: [h('p', {text: 'Your team of ' + team.length + ' is running. Each bot has a role; the team agreement says how they work together.'}), teamList(team)]});
  else out.push({t: 'The team of bots', st: 'done', body: [h('p', null, 'Your team is the admin bot for now. You can add more bots on the workspace’s ', h('a', {href: '/workspaces/admin', text: 'Team tab'}), '.')]});
  return out;
}
function teamList(team) {
  return h('ul', {'class': 'team'}, team.map(function (b) { return h('li', null, h('span', {'class': 'nm', text: b.handle}), h('span', {text: b.role || b.harness}), botTag(kind(b))); }));
}
function workStep(s) {
  if (work) return work;
  textBox = h('textarea', {'aria-label': 'First message to the team'}); textBox.value = FIRST;
  sendBtn = h('button', {type: 'button', 'class': 'primary', text: 'Send and open General'});
  sendNote = h('span', {'class': 'dim', style: 'font-size:12.5px'});
  sendBtn.addEventListener('click', send);
  work = h('li', {'class': 'step todo'}, h('span', {'class': 'num', 'aria-hidden': 'true'}, '5'),
    h('div', null, h('h3', null, 'Give them work'),
      h('p', null, 'Send a first message. It goes to General in the Admin workspace and mentions no one, so a developer picks it up and explains the team and its agreement. You will be taken to General. You can change the words.'),
      textBox, h('div', {'class': 'row'}, sendBtn, sendNote)));
  return work;
}
function toGeneral() { location.href = '/c/' + encodeURIComponent((F && F.steps.general) || 'admin'); }
function send() {
  var body = textBox.value.trim(); if (!body) return;
  sendBtn.disabled = true; sendNote.className = 'dim'; sendNote.textContent = 'Sending…';
  api('POST', '/api/messages', {channel: F.steps.general, body: body})
    .then(function () { return api('PUT', '/api/first-run', {state: 'done'}).catch(function () {}); })
    .then(toGeneral, function (e) { sendBtn.disabled = false; sendNote.className = 'msg-err'; sendNote.setAttribute('role', 'alert'); sendNote.textContent = e.message; });
}
function skip(ev) {
  if (ev) ev.preventDefault();
  api('PUT', '/api/first-run', {state: 'skipped'}).then(toGeneral, function (e) { alert(e.message); });
}
function draw(d) {
  F = d; var s = d.steps, list = steps(s), done = list.filter(function (x) { return x.st === 'done'; }).length;
  var total = list.filter(function (x) { return x.st !== 'skip'; }).length + 1;
  var admin = kind(s.bot) === 'running';
  var skipBtn = h('button', {type: 'button', 'class': 'skip', text: 'Skip setup'}); skipBtn.addEventListener('click', skip);
  var items = list.map(function (x, i) {
    return h('li', {'class': 'step ' + x.st}, h('span', {'class': 'num', 'aria-hidden': 'true'}, x.st === 'done' ? '✓' : x.st === 'bad' ? '!' : String(i + 1)), h('div', null, h('h3', null, x.t, ' ', x.tag || null), x.body));
  });
  var w = workStep(s); w.className = 'step ' + (admin ? 'cur' : 'todo');
  sendBtn.disabled = kind(s.bot) === 'failed';
  sendNote.textContent = admin ? '' : (kind(s.bot) === 'login' ? 'It will be read once the admin bot has a login.' : 'The bots read it as they start; you do not have to wait.');
  items.push(w);
  var again = h('a', {href: '/c/' + encodeURIComponent(s.general || 'admin'), text: 'Skip to General now'}); again.addEventListener('click', skip);
  root.textContent = '';
  root.appendChild(h('div', {'class': 'head'}, h('div', null, h('h1', {text: 'Welcome, ' + s.board.handle + '. Let’s get bot_board working.'}),
    h('p', {'class': 'lead', text: 'This page is shown once. It updates by itself while your bots start. Sending the message takes you to General, where a developer answers.'})), skipBtn));
  root.appendChild(h('div', {'class': 'prog', role: 'img', 'aria-label': done + ' of ' + total + ' steps done'}, h('span', {text: done + ' of ' + total + ' done'}), h('i', null, h('b', {style: 'width:' + Math.round(100 * done / total) + '%'}))));
  root.appendChild(h('ol', {'class': 'steps'}, items));
  root.appendChild(h('p', {'class': 'toast'}, again, '. Skipping is saved on the board for you, so this page does not come back. You can open it again from Setup guide, at the bottom of every page.'));
  clearTimeout(timer);
  if (kind(s.bot) !== 'running' || (s.team || []).some(function (b) { return kind(b) !== 'running'; })) timer = setTimeout(load, 5000);
}
function load() {
  if (!ME || !ME.token) { root.textContent = ''; root.appendChild(h('p', null, 'Sign in on the ', h('a', {href: '/', text: 'board page'}), ' first, then come back.')); return Promise.resolve(); }
  return api('GET', '/api/first-run').then(function (d) {
    if (!d.steps) { root.textContent = 'There is no setup guide on this board.'; return; }
    draw(d);
  }, function (e) { root.textContent = ''; root.appendChild(h('p', {'class': 'msg-err', role: 'alert', text: e.message})); clearTimeout(timer); timer = setTimeout(load, 10000); });
}
load();
"""


def welcome_page(board_name: str) -> str:
    return shell(board_name, "Welcome", f'<style>{WELCOME_CSS}</style><div class="fr" id="fr"><p class="dim">Loading…</p></div><noscript><p>This page needs JavaScript. <a href="/c/admin">Go to the board</a>.</p></noscript><script>{WELCOME_JS}</script>')
