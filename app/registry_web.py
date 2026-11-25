"""Skills and profiles on /fleet, and their pages under /fleet/skills/ and /fleet/profiles/.

The fleet page gets two sections between Machines and Versions: Skills (every skill,
its revision and the profiles that list it; + Add a skill opens an inline panel with
one form, the skill written there) and Profiles (every profile, its skills and
the bots that carry it; view, clone, and delete only when no bot carries it). Each
skill has a page with an editor, history and who uses it; each profile a page with
an edit form for what it says and its skills (#397), its bots, the text a bot reads,
its history and its lineage. One form, /fleet/profiles/new, creates a profile from a template, a blank, or
an existing profile (which is what Clone is).

Writes go through the API with the identity the board page keeps in this browser
(localStorage bb.me). Data from the API only ever lands on the page as text.
"""
from __future__ import annotations

from . import registry
from .web import FLEET_CSS, ago, e, shell

STARS = "★"


# ---------------------------------------------------------------- pieces


def _revision(skill: dict) -> str:
    return f'rev {skill["revision"]} · {e(ago(skill["updated_at"]))} · {e(skill["author"])}'


def _used_by(skill: dict, bots_of: dict) -> str:
    names = skill.get("used_by") or []
    if not names:
        return '<span class="dim">no profile uses it</span>'
    n = sum(len(bots_of.get(p, [])) for p in names)
    chips = " ".join(f'<a class="chip" href="/fleet/profiles/{e(p)}">{e(p)}</a>' for p in names)
    return f'<div class="chips">{chips}</div><div class="by">{n} bot{"s" * (n != 1)}</div>'


def _skill_chip(s: dict) -> str:
    star = f' <span class="star" title="mandatory">{STARS}</span>' if s.get("mandatory") else ""
    return f'<a class="chip" href="/fleet/skills/{e(s["name"])}">{e(s["name"])}{star}</a>'


# ---------------------------------------------------------- fleet sections

ADD_SKILL_PANEL = """<details id="addskill" class="addbot-cta">
<summary><span class="addbot-btn">+ Add a skill</span></summary>
<form class="reg-form" id="skill-add" aria-label="Add a skill">
<label class="fld"><span>Name <small>· slug, this is how profiles name it</small></span>
<input name="name" required pattern="[a-z0-9][a-z0-9_\\-]{1,63}" placeholder="art-to-3d" autocomplete="off"></label>
<label class="fld"><span>Description <small>· one line; this is what a bot sees in its profile</small></span>
<input name="description" required maxlength="500" placeholder="Turn approved concept art into a game-ready 3D model." autocomplete="off"></label>
<label class="fld"><span>Instructions <small>· Markdown; the body of SKILL.md, or a whole SKILL.md pasted in. Name and description become its frontmatter.</small></span>
<textarea name="body" required rows="12" placeholder="## When to use this&#10;&#10;## Steps&#10;1. &#10;&#10;## What not to do"></textarea></label>
<div class="row"><button type="submit" class="addbot-go">Add skill</button>
<span class="note" id="skill-note">It appears in the list; a profile created or cloned from now on can tick it.</span></div>
<p class="status-line" role="status" aria-live="polite"></p>
</form>
</details>"""


def skills_section(skills: list, profiles: list) -> str:
    bots_of = {p["name"]: p.get("bots") or [] for p in profiles}
    def table(group, used_by=True):
        rows = []
        for s in group:
            page = f'/fleet/skills/{e(s["name"])}'
            acts = f'<a class="act" href="{page}">edit</a><a class="act" href="{page}#history">history</a>'
            rows.append(f'<tr id="skill-{e(s["name"])}"><td class="who"><a href="{page}">{e(s["name"])}</a>'
                        f'<div class="by desc">{e(s["description"])}</div></td>'
                        f'<td data-label="revision" class="nw">{_revision(s)}</td>'
                        + (f'<td data-label="used by">{_used_by(s, bots_of)}</td>' if used_by else "")
                        + f'<td class="acts">{acts}</td></tr>')
        return ('<table class="bots skills"><thead><tr><th>Skill</th><th>Revision</th>' + ("<th>Used by</th>" if used_by else "")
                + '<th><span class="sr-only">Actions</span></th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>')
    base = [s for s in skills if s["base"]]
    rest = [s for s in skills if not s["base"]]
    tables = (table(skills) if skills else '<div class="empty">No skill yet. Add one above.</div>') if not base else (
        f'<h3 class="skill-group" id="base-skills">Base skills · every bot · {len(base)}</h3>{table(base, used_by=False)}'
        + (f'<h3 class="skill-group">Profile skills · {len(rest)}</h3>{table(rest)}' if rest else ""))
    return (f'<section class="fleet-section reg" id="fleet-skills"><div class="sec-head"><h2>Skills · {len(skills)}</h2>'
            f'{ADD_SKILL_PANEL}</div>'
            '<p class="sub">Fleet-wide, and the board is their only home. A bot gets the skills of its profile when its '
            'session starts, and a base skill whatever its profile; an edit reaches every bot on it at its next prompt, which tells it to read the skill again.</p>'
            f'{tables}</section>')


SKILLS_SHELL_JS = r"""
(function () {
  var box = document.getElementById('skills-list');
  if (!box || !document.querySelector('.fleet-page[data-page=skills]')) return;
  var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
  function get(path) {
    return fetch(path, {headers: ME && ME.token ? {'Authorization': 'Bearer ' + ME.token} : {}})
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || d.detail || ('HTTP ' + r.status)); return d; }); });
  }
  function el(tag, attrs) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') n.textContent = attrs[k]; else n.setAttribute(k, attrs[k]); });
    for (var i = 2; i < arguments.length; i++) if (arguments[i] != null) n.appendChild(typeof arguments[i] === 'string' ? document.createTextNode(arguments[i]) : arguments[i]);
    return n;
  }
  function ago(iso) {
    var t = Date.parse(iso); if (isNaN(t)) return iso || 'never';
    var s = (Date.now() - t) / 1000;
    return s < 60 ? Math.floor(s) + 's ago' : s < 3600 ? Math.floor(s / 60) + 'm ago' : s < 86400 ? Math.floor(s / 3600) + 'h ago' : Math.floor(s / 86400) + 'd ago';
  }
  function usedBy(s, botsOf) {
    var names = s.used_by || [];
    if (!names.length) return el('span', {'class': 'dim', text: 'no profile uses it'});
    var n = names.reduce(function (a, p) { return a + (botsOf[p] || []).length; }, 0), chips = el('div', {'class': 'chips'});
    names.forEach(function (p, i) { if (i) chips.appendChild(document.createTextNode(' ')); chips.appendChild(el('a', {'class': 'chip', href: '/fleet/profiles/' + encodeURIComponent(p), text: p})); });
    return el('div', null, chips, el('div', {'class': 'by', text: n + ' bot' + (n === 1 ? '' : 's')}));
  }
  function table(group, withUsed, botsOf) {
    var head = el('tr', null, el('th', {text: 'Skill'}), el('th', {text: 'Revision'}), withUsed ? el('th', {text: 'Used by'}) : null,
                  el('th', null, el('span', {'class': 'sr-only', text: 'Actions'}))), body = el('tbody');
    group.forEach(function (s) {
      var page = '/fleet/skills/' + encodeURIComponent(s.name);
      var cells = [el('td', {'class': 'who'}, el('a', {href: page, text: s.name}), el('div', {'class': 'by desc', text: s.description || ''})),
                   el('td', {'data-label': 'revision', 'class': 'nw', text: 'rev ' + s.revision + ' \u00b7 ' + ago(s.updated_at) + ' \u00b7 ' + s.author})];
      if (withUsed) { var u = el('td', {'data-label': 'used by'}); u.appendChild(usedBy(s, botsOf)); cells.push(u); }
      cells.push(el('td', {'class': 'acts'}, el('a', {'class': 'act', href: page, text: 'edit'}), el('a', {'class': 'act', href: page + '#history', text: 'history'})));
      var tr = el('tr', {id: 'skill-' + s.name}); cells.forEach(function (c) { tr.appendChild(c); }); body.appendChild(tr);
    });
    return el('table', {'class': 'bots skills'}, el('thead', null, head), body);
  }
  function draw(skills, profiles) {
    var botsOf = {}; profiles.forEach(function (p) { botsOf[p.name] = p.bots || []; });
    var base = skills.filter(function (s) { return s.base; }), rest = skills.filter(function (s) { return !s.base; });
    document.getElementById('skills-count').textContent = skills.length + ' skills';
    document.getElementById('skills-h').textContent = 'Skills \u00b7 ' + skills.length;
    box.textContent = ''; box.className = '';
    if (!skills.length) { box.appendChild(el('div', {'class': 'empty', text: 'No skill yet. Add one above.'})); return; }
    if (!base.length) { box.appendChild(table(skills, true, botsOf)); return; }
    box.appendChild(el('h3', {'class': 'skill-group', id: 'base-skills', text: 'Base skills \u00b7 every bot \u00b7 ' + base.length}));
    box.appendChild(table(base, false, botsOf));
    if (rest.length) { box.appendChild(el('h3', {'class': 'skill-group', text: 'Profile skills \u00b7 ' + rest.length})); box.appendChild(table(rest, true, botsOf)); }
    var there = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (there) there.scrollIntoView({block: 'nearest'});
  }
  Promise.all([get('/api/skills'), get('/api/profiles')]).then(function (r) { draw(r[0].skills || [], r[1].profiles || []); },
    function (e) { box.className = 'sub'; box.textContent = 'Could not load the skills: ' + e.message; document.getElementById('skills-count').textContent = ''; });
})();
"""

REG_SECTION_JS = r"""
(function () {
  var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
  function need() { if (!ME) alert('Sign in on the board page first (the "sign in to post" button), then come back.'); return !!ME; }
  function call(method, path, body) {
    return fetch(path, {method: method, headers: {'content-type': 'application/json', 'Authorization': 'Bearer ' + (ME ? ME.token : '')},
                        body: body ? JSON.stringify(body) : undefined})
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || d.detail || ('refused: ' + r.status)); return d; }); });
  }
  var form = document.getElementById('skill-add');
  if (form) {
    var msg = form.querySelector('.status-line');
    form.addEventListener('submit', function (ev) {
      ev.preventDefault(); if (!need()) return;
      var name = form.elements.name.value.trim().toLowerCase();
      msg.textContent = 'Saving…';
      call('PUT', '/api/skills/' + encodeURIComponent(name),
           {body: form.elements.body.value, fields: {description: form.elements.description.value.trim()},
            base_revision: 0, reason: 'added on the Skills page'})
      .then(function () { location.href = '/fleet/skills/' + encodeURIComponent(name); },
            function (e) { msg.textContent = e.message; });
    });
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button.del-profile'); if (!b || !need()) return;
    if (!confirm('Delete the profile ' + b.dataset.name + '? No bot carries it. The name is never reused.')) return;
    call('DELETE', '/api/profiles/' + encodeURIComponent(b.dataset.name))
      .then(function () { location.reload(); }, function (e) { alert(e.message); });
  });
})();
"""

REG_CSS = """
.fleet-page .reg .sec-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.fleet-page .reg .sec-head h2{margin-top:6px}
.fleet-page .reg .desc{color:var(--dim);font-family:var(--sans);white-space:normal;max-width:46em}
.fleet-page .reg td.who{white-space:normal}
.fleet-page .reg .chip{text-decoration:none;color:var(--ink)}
.fleet-page .reg .chip .star{color:var(--accent)}
.fleet-page .reg .acts a.act{text-decoration:none;display:inline-block}
.fleet-page .reg a.addbot-btn{text-decoration:none}
body.out .fleet-page .needs-me,body.out .fleet-page details#addskill{display:none}
.fleet-page details#addskill>summary{list-style:none;cursor:pointer}
.fleet-page details#addskill>summary::-webkit-details-marker{display:none}
.fleet-page details#addskill[open]{flex-basis:100%;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:0 0 12px;padding:10px 0 0}
.fleet-page details#addskill[open]>summary{padding:0 16px 8px}
.fleet-page .tabs{display:flex;gap:4px;padding:0 16px;border-bottom:1px solid var(--line)}
.fleet-page .tabs .tab{background:none;border:0;border-bottom:2px solid transparent;padding:6px 10px;font:600 13px var(--sans);color:var(--dim);cursor:pointer}
.fleet-page .tabs .tab[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent)}
.fleet-page .reg-form{display:flex;flex-direction:column;gap:12px;padding:12px 16px 14px}
.fleet-page .reg-form .fld{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--dim)}
.fleet-page .reg-form .fld small{color:var(--dim);font-weight:400}
.fleet-page .reg-form input,.fleet-page .reg-form textarea,.fleet-page .reg-form select{background:var(--bg);border:1px solid var(--line);
 color:var(--ink);border-radius:6px;padding:6px 8px;font:13px/1.45 var(--mono);width:100%;box-sizing:border-box}
.fleet-page .reg-form textarea{resize:vertical;white-space:pre-wrap}
.fleet-page .reg-form .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.fleet-page .reg-form .note{font-size:12px;color:var(--dim)}
.fleet-page .reg-form .status-line{margin:0;font-size:13px;color:var(--accent);min-height:1em}
.fleet-page .reg-form .status-line.ok{color:var(--good)}
.fleet-page .reg-form button:disabled{opacity:.5;cursor:default}
.fleet-page .crumbs{font-size:13px;color:var(--dim);margin:0 0 10px}.fleet-page .crumbs a{color:var(--dim)}
.fleet-page .reg-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 6px}
.fleet-page .reg-head h1{margin:0;font-family:var(--mono)}
.fleet-page p.role{margin:0 0 6px;font-size:15px}
.fleet-page .reg-head .right{margin-left:auto;display:flex;gap:8px;align-items:center;font-size:13px;color:var(--dim)}
.fleet-page .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 18px}
.fleet-page .card h2{margin:0 0 10px}
.fleet-page .card dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:0;font-size:13.5px}
.fleet-page .card dt{color:var(--dim);font:12px var(--mono)}.fleet-page .card dd{margin:0;max-width:60em}
.fleet-page .card dd p{margin:0 0 6px}.fleet-page .card dd p:last-child{margin-bottom:0}
.fleet-page .card ul.hist{list-style:none;margin:0;padding:0}
.fleet-page .card ul.hist li{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--line);font-size:13px}
.fleet-page .card table.bots{table-layout:auto}.fleet-page .card{min-width:0;overflow:hidden}
.fleet-page .card ul.hist li:first-child{border-top:0}
.fleet-page .card ul.hist .rev{font-family:var(--mono);white-space:nowrap}
.fleet-page .card ul.hist .reason{flex:1;min-width:12em}
.fleet-page .card.danger{border-color:var(--accent)}
.fleet-page .card.danger p{margin:4px 0 0;font-size:13px;color:var(--dim)}
.fleet-page pre.what{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;white-space:pre-wrap;font-size:12.5px;max-height:28em;overflow:auto}
.fleet-page .md h3,.fleet-page .md h4,.fleet-page .md h5{margin:14px 0 6px}.fleet-page .md p{margin:6px 0}
.fleet-page .md pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px 10px;overflow:auto}
.fleet-page .two{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:18px}
@media(max-width:900px){.fleet-page .two{grid-template-columns:1fr}}
.fleet-page .skill-group{margin:16px 0 6px;font-size:13px}
.fleet-page .seg{display:inline-flex;border:1px solid var(--line);border-radius:7px;overflow:hidden;align-self:flex-start;width:fit-content}
.fleet-page .seg label{display:block;white-space:nowrap;padding:6px 12px;font-size:13px;cursor:pointer;margin:0}
.fleet-page .seg label:has(input:checked){background:var(--ink);color:var(--panel)}
.fleet-page .seg label:has(input:focus-visible){outline:2px solid var(--accent);outline-offset:-2px}
.fleet-page .seg input{position:absolute;opacity:0;width:1px;height:1px}
.fleet-page .starters{display:flex;flex-wrap:wrap;gap:6px}
.fleet-page .starters button{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font:13px var(--sans);cursor:pointer}
.fleet-page .starters button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent);font-weight:600}
.fleet-page table.bots.pick td{vertical-align:middle}
.fleet-page table.bots.pick input[type=checkbox]{width:auto}
"""


# --------------------------------------------------------- the three pages
#
# One way a page gets its data (docs/architecture.md): the route sends an empty shell and the page's script reads
# /api with the identity this browser keeps (#860). The renderers are plain functions of the data, which return an HTML
# string with every value through esc(), so a test can run them under node without a browser.

REG_COMMON_JS = r"""
var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
if (!ME) document.body.classList.add('out');
function need() { if (!ME) alert('Sign in on the board page first (the "sign in to post" button), then come back.'); return !!ME; }
function call(method, path, body) {
  var headers = {'content-type': 'application/json'};
  if (ME) headers['Authorization'] = 'Bearer ' + ME.token;
  return fetch(path, {method: method, headers: headers, body: body ? JSON.stringify(body) : undefined})
    .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok) { var err = new Error(d.error || d.detail || ('refused: ' + r.status)); err.status = r.status; throw err; } return d; }); });
}
var ENT = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;'};
function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return ENT[c]; }); }
function ago(ts) {
  if (!ts) return 'never';
  var then = new Date(ts); if (isNaN(then)) return String(ts);
  var secs = (Date.now() - then.getTime()) / 1000;
  if (secs < 60) return Math.floor(secs) + 's ago';
  if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
  if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
  return Math.floor(secs / 86400) + 'd ago';
}
function plural(n, word, many) { return n + ' ' + (n === 1 ? word : (many || word + 's')); }
function showMissing(root, what, err) {
  root.innerHTML = '<p class="sub">' + (err && err.status === 404 ? esc('no ' + what + ' with that name') : esc(err && err.message || 'could not load it')) + '</p>';
}
"""

# ------------------------------------------------------------ the skill page

SKILL_PAGE_JS = REG_COMMON_JS + r"""
var NAME = decodeURIComponent(location.pathname.split('/').pop());
// the same Markdown subset the board renders: headings, fenced code, lists, paragraphs
function inline(s) { return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>'); }
function md(text) {
  var out = [], para = [], list = null, code = null;
  function flush() { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } }
  function close() { if (list) { out.push('</' + list + '>'); list = null; } }
  text.split('\n').forEach(function (raw) {
    var line = raw.replace(/\s+$/, ''), m;
    if (code !== null) { if (line.indexOf('```') === 0) { out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>'); code = null; } else code.push(line); return; }
    if (line.indexOf('```') === 0) { flush(); close(); code = []; return; }
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { flush(); close(); var n = Math.min(m[1].length + 2, 6); out.push('<h' + n + '>' + inline(m[2]) + '</h' + n + '>'); return; }
    if ((m = line.match(/^\s*([-*]|\d+[.)])\s+(.*)$/))) { flush(); var k = /^[-*]$/.test(m[1]) ? 'ul' : 'ol'; if (list !== k) { close(); out.push('<' + k + '>'); list = k; } out.push('<li>' + inline(m[2]) + '</li>'); return; }
    if (!line.trim()) { flush(); close(); return; }
    if (list && /^(\s{2,}|\t)/.test(raw)) { out[out.length - 1] = out[out.length - 1].slice(0, -5) + ' ' + inline(line.trim()) + '</li>'; return; }
    close(); para.push(line.trim());
  });
  flush(); close(); if (code !== null) out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>');
  return out.join('\n');
}
// D: {skill, bots, history, profiles, text}: what GET /api/skills/<name>?json, its history, /api/profiles and the SKILL.md give
function skillHtml(D) {
  var skill = D.skill, name = esc(skill.name), base = !!skill.base, used = skill.used_by || [], bots = D.bots || [], history = D.history || [];
  var botsOf = {}; (D.profiles || []).forEach(function (p) { botsOf[p.name] = p.bots || []; });
  var usedHtml = used.map(function (p) {
    var b = botsOf[p] || [];
    return '<li><a class="chip" href="/fleet/profiles/' + esc(p) + '">' + esc(p) + '</a> <span class="dim">' + plural(b.length, 'bot')
      + (b.length ? ' · ' + b.map(function (h) { return '@' + esc(h); }).join(', ') : '') + '</span></li>';
  }).join('') || '<li class="dim">No profile lists it yet. A profile created or cloned from now on can.</li>';
  var histHtml = history.map(function (h) {
    return '<li><span class="rev">rev ' + h.revision + '</span><span class="dim nw">' + esc(ago(h.updated_at)) + ' · ' + esc(h.author) + '</span>'
      + '<span class="reason">' + (h.reason ? esc(h.reason) : '<span class=dim>no reason given</span>') + (h.base ? ' <span class=dim>· every bot</span>' : '') + '</span>'
      + (h.revision !== skill.revision && !h.deleted ? '<button type="button" class="act restore needs-me" data-rev="' + h.revision + '">restore</button>' : '')
      + (h.deleted ? '<span class=dim>deleted</span>' : '') + '</li>';
  }).join('');
  if (base) usedHtml = '<li>every bot <span class="dim">' + bots.length + ' on the fleet' + (bots.length ? ' · ' + bots.map(function (h) { return '@' + esc(h); }).join(', ') : '') + '</span></li>'
    + (used.length ? '<li class="dim">Also listed by ' + used.map(esc).join(', ') + '.</li>' : '');
  var danger = '<button type="button" class="act del-skill" data-name="' + name + '" data-rev="' + skill.revision + '"' + (used.length || base ? ' disabled' : '') + '>Delete this skill</button>'
    + (base ? '<p>Refused while every bot has it. Make it profile-only first.</p>'
       : used.length ? '<p>Refused while ' + plural(used.length, 'profile') + ' list' + (used.length !== 1 ? '' : 's') + ' it: ' + used.map(esc).join(', ') + '. Edit ' + (used.length !== 1 ? 'them' : 'it') + ' to drop it, then come back.</p>'
       : '<p>No profile lists it. History is kept; the name can be used again.</p>');
  return '<p class="crumbs"><a href="/skills">Skills</a> › ' + name + '</p>'
    + '<div class="reg-head"><h1>' + name + '</h1><div class="right">rev ' + skill.revision + ' · ' + esc(ago(skill.updated_at)) + ' · ' + esc(skill.author) + '</div></div>'
    + '<p class="sub">' + esc(skill.description) + '</p>'
    + '<div class="two"><div><div class="card" id="editor">'
    + '<div class="tabs" role="tablist" style="padding:0;margin:0 0 10px"><button type="button" class="tab" role="tab" aria-selected="true" data-tab="edit">Edit</button>'
    + '<button type="button" class="tab" role="tab" aria-selected="false" data-tab="preview">Preview</button><button type="button" class="tab" role="tab" aria-selected="false" data-tab="gets">What a bot gets</button></div>'
    + '<form class="reg-form" id="skill-edit" style="padding:0"><label class="fld"><span>Name</span><input name="name" value="' + name + '" readonly></label>'
    + '<div class="fld"><span id="who-label">Who gets it</span><div class="seg" role="radiogroup" aria-labelledby="who-label">'
    + '<label><input type="radio" name="who" value="profiles"' + (base ? '' : ' checked') + '> Profiles that list it</label>'
    + '<label><input type="radio" name="who" value="base"' + (base ? ' checked' : '') + '> Every bot</label></div><small class="note" id="who-note"></small></div>'
    + '<label class="fld"><span>Description <small>· one line; this is what a bot sees in its profile</small></span><input name="description" value="' + esc(skill.description) + '" maxlength="500" required></label>'
    + '<label class="fld"><span>Instructions <small>· Markdown; the body of SKILL.md</small></span><textarea name="body" rows="22" required>' + esc(skill.body) + '</textarea></label>'
    + '<label class="fld"><span>Reason for this change <small>· required, as in a knowledge write; it shows in History</small></span><input name="reason" required maxlength="500" placeholder="what changed, and why" autocomplete="off"></label>'
    + '<div class="row needs-me"><button type="submit" class="addbot-go">Save as rev ' + (skill.revision + 1) + '</button><span class="note">Bots on it are told at their next prompt.</span></div>'
    + '<p class="status-line" role="status" aria-live="polite"></p></form>'
    + '<div class="md" id="skill-preview" hidden>' + md(skill.body) + '</div>'
    + '<div id="skill-gets" hidden><p class="sub">Exactly what <code>GET /api/skills/' + name + '</code> returns, and the line a profile carries for it.</p>'
    + '<pre class="what" id="skill-text">' + esc(D.text) + '</pre><pre class="what">- **' + name + '**' + (base ? ' (mandatory, every bot)' : '') + ': ' + esc(skill.description) + '</pre></div>'
    + '</div></div><div>'
    + '<div class="card"><h2>Details</h2><dl><dt>read at</dt><dd><code>GET /api/skills/' + name + '</code></dd><dt>revision</dt><dd>' + skill.revision + ' · ' + esc(ago(skill.updated_at)) + ' · ' + esc(skill.author) + '</dd>'
    + '<dt>who gets it</dt><dd>' + (base ? 'every bot' : 'profiles that list it') + '</dd></dl></div>'
    + '<div class="card"><h2>Used by · ' + (base ? 'every bot' : used.length) + '</h2><ul class="hist">' + usedHtml + '</ul>'
    + '<p class="sub" style="margin:8px 0 0">Read-only here: a profile\'s skill list is edited on the profile\'s page.</p>'
    + (bots.length ? '<button type="button" class="act needs-me" id="reset-all" style="margin-top:8px">New session for all bots on it</button>' : '')
    + '<p class="status-line" id="reset-note" role="status" aria-live="polite"></p></div>'
    + '<div class="card" id="history"><h2>History · ' + history.length + '</h2><ul class="hist">' + histHtml + '</ul>'
    + '<p class="sub" style="margin:8px 0 0">Restore writes a new revision with the old body; nothing is rewritten in place.</p></div>'
    + '<div class="card danger"><h2>Danger</h2>' + danger + '</div></div></div>';
}
function wireSkill(S, form) {
  function resetAll(path, bots, note) {
    if (!need()) return;
    if (!confirm('Give ' + bots.map(function (h) { return '@' + h; }).join(', ') + ' a new session? Each reads its profile and skills again (when idle; a stopped bot at its next start).')) return;
    call('POST', path).then(function (d) {
      var n = Object.keys(d.skipped);
      note.className = 'status-line ok';
      note.textContent = d.reset.length + ' asked' + (n.length ? '; skipped: ' + n.map(function (h) { return '@' + h + ' (' + d.skipped[h] + ')'; }).join(', ') : '') + '.';
    }, function (e) { note.className = 'status-line'; note.textContent = e.message; });
  }
  var all = document.getElementById('reset-all');
  if (all) all.addEventListener('click', function () {
    resetAll('/api/skills/' + encodeURIComponent(S.name) + '/reset', S.bots, document.getElementById('reset-note'));
  });
  function text() {
    var d = form.elements.description.value.trim();
    var q = /[:#"'\n]/.test(d) || !d ? JSON.stringify(d) : d;
    return '---\nname: ' + S.name + '\ndescription: ' + q + '\n---\n\n' + form.elements.body.value.trim() + '\n';
  }
  document.querySelectorAll('#editor button.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('#editor button.tab').forEach(function (b) { b.setAttribute('aria-selected', String(b === t)); });
      form.hidden = t.dataset.tab !== 'edit';
      var p = document.getElementById('skill-preview'), g = document.getElementById('skill-gets');
      p.hidden = t.dataset.tab !== 'preview'; g.hidden = t.dataset.tab !== 'gets';
      if (!p.hidden) p.innerHTML = md(form.elements.body.value);
      if (!g.hidden) { document.getElementById('skill-text').textContent = text(); }
    });
  });
  function whoNote() {
    document.getElementById('who-note').textContent = form.elements.who.value === 'base'
      ? 'Every bot: mandatory for all, read before work, including bots with no profile. Changes at save, with the reason below.'
      : 'The bots whose profile lists it. Changes at save, with the reason below.';
  }
  form.addEventListener('change', whoNote); whoNote();
  form.addEventListener('submit', function (ev) {
    ev.preventDefault(); if (!need()) return;
    var msg = form.querySelector('.status-line'); msg.className = 'status-line'; msg.textContent = 'Saving…';
    call('PUT', '/api/skills/' + encodeURIComponent(S.name),
         {body: form.elements.body.value, fields: {description: form.elements.description.value.trim(), base: form.elements.who.value === 'base'},
          base_revision: S.revision, reason: form.elements.reason.value.trim()})
    .then(function (d) {
      msg.className = 'status-line ok';
      msg.textContent = 'Saved as rev ' + d.skill.revision + '. ' + (S.bots.length ? S.bots.length + ' bot' + (S.bots.length === 1 ? '' : 's') + ' on it read it again at their next prompt.' : 'No bot has a profile that lists it yet.');
      setTimeout(function () { location.reload(); }, 1200);
    }, function (e) { msg.textContent = e.message; });
  });
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button.restore'); if (!b || !need()) return;
    var rev = b.dataset.rev;
    if (!confirm('Restore rev ' + rev + ' as a new revision ' + (S.revision + 1) + '? Nothing is rewritten; the revisions between stay in the history.')) return;
    call('GET', '/api/skills/' + encodeURIComponent(S.name) + '?json&revision=' + rev).then(function (d) {
        return call('PUT', '/api/skills/' + encodeURIComponent(S.name),
                    {body: d.skill.body, fields: {description: d.skill.description}, base_revision: S.revision, reason: 'restored rev ' + rev});
      }).then(function () { location.reload(); }, function (e) { alert(e.message); });
  });
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button.del-skill'); if (!b || !need()) return;
    if (!confirm('Delete the skill ' + S.name + '? Its history is kept.')) return;
    call('DELETE', '/api/skills/' + encodeURIComponent(S.name), {base_revision: S.revision, reason: 'deleted on the Skills page'})
      .then(function () { location.href = '/skills'; }, function (e) { alert(e.message); });
  });
}
// r: [GET /api/skills/<n>?json, its /history, GET /api/profiles, GET /api/skills/<n> (the SKILL.md)]
function composeSkill(r) { return {skill: r[0].skill, bots: r[0].bots, history: r[1].revisions, profiles: r[2].profiles, text: r[3]}; }
function loadSkill() {
  var root = document.getElementById('reg'), url = '/api/skills/' + encodeURIComponent(NAME);
  Promise.all([call('GET', url + '?json'), call('GET', url + '/history'), call('GET', '/api/profiles'),
               fetch(url, {headers: ME ? {'Authorization': 'Bearer ' + ME.token} : {}}).then(function (r) { return r.ok ? r.text() : ''; })])
  .then(function (r) {
    var D = composeSkill(r);
    document.title = 'skill ' + D.skill.name;
    root.innerHTML = skillHtml(D);
    wireSkill({name: D.skill.name, revision: D.skill.revision, bots: D.bots}, document.getElementById('skill-edit'));
  }, function (e) { showMissing(root, 'skill', e); });
}
if (typeof document !== 'undefined' && document.getElementById('reg')) loadSkill();
"""


# --------------------------------------------------------- the profile page

PROFILE_PAGE_JS = REG_COMMON_JS + r"""
var NAME = decodeURIComponent(location.pathname.split('/').pop());
// D: {profile (with base_skills, text), history, skills, bots, profiles, templates}
function profileHtml(D) {
  var profile = D.profile, name = esc(profile.name), skills = D.skills || [], bots = D.bots || [], history = D.history || [];
  var also = profile.base_skills || [], alsoNames = {}; also.forEach(function (s) { alsoNames[s.name] = true; });
  var own = {}, ownCount = 0;
  (profile.skills || []).forEach(function (s) { if (!alsoNames[s.name]) { own[s.name] = !!s.mandatory; ownCount++; } });
  var skillRows = skills.filter(function (s) { return !s.base; }).map(function (s) {
    return '<tr><td><input type="checkbox" name="skill" value="' + esc(s.name) + '" aria-label="include ' + esc(s.name) + '"' + (s.name in own ? ' checked' : '') + '></td>'
      + '<td class="who"><a href="/fleet/skills/' + esc(s.name) + '">' + esc(s.name) + '</a><div class="by desc">' + esc(s.description) + '</div></td>'
      + '<td data-label="mandatory"><label><input type="checkbox" name="mandatory" value="' + esc(s.name) + '" aria-label="' + esc(s.name) + ' mandatory"' + (own[s.name] ? ' checked' : '') + '> read before any work</label></td>'
      + '<td data-label="revision" class="nw dim">rev ' + s.revision + '</td></tr>';
  }).join('');
  var skillsHtml = skillRows ? '<table class="bots pick"><thead><tr><th><span class="sr-only">Include</span></th><th>Skill</th><th>Mandatory</th><th>Rev</th></tr></thead><tbody>' + skillRows + '</tbody></table>'
    : '<p class="sub">The registry has no profile skill yet; its bots have the guide and their workspace\'s knowledge base.</p>';
  if (also.length) skillsHtml += '<p class="sub" id="base-skills">Base skills, every bot has them: ' + also.map(function (s) { return '<a class="chip" href="/fleet/skills/' + esc(s.name) + '">' + esc(s.name) + '</a>'; }).join(', ')
    + '. Not this profile\'s: set on the skill itself.</p>';
  var botRows = bots.map(function (b) {
    var st = b.state || {}, tone = {working: 'run', waiting: 'ok', stopped: '', unknown: ''}[st.state]; if (tone === undefined) tone = 'warn';
    return '<tr><td class="who"><a href="/a/' + esc(b.handle) + '">@' + esc(b.handle) + '</a></td>'
      + '<td data-label="workspace">' + (b.workspace ? '<a href="/workspaces/' + esc(b.workspace) + '">' + esc(b.workspace) + '</a>' : '—') + '</td>'
      + '<td data-label="status"><span class="pip ' + tone + '"></span>' + esc(st.words || b.status || '') + '</td>'
      + '<td data-label="last run" class="dim">' + (b.last_run ? esc(ago(b.last_run)) : 'never') + '<div class="by">on <a href="/machines#box-' + esc(b.host) + '">' + esc(b.host) + '</a></div></td>'
      + '<td class="acts"><button type="button" class="reset" data-handle="' + esc(b.handle) + '" title="set its harness session aside; it reads the profile and its skills again">new session</button></td></tr>';
  }).join('');
  var n = bots.length;
  var botsHtml = n ? '<table class="bots"><thead><tr><th>Bot</th><th>Workspace</th><th>Status</th><th>Last run</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>' + botRows + '</tbody></table>'
    : '<p class="sub">No bot carries it yet. Pick it under Profile when you add a bot.</p>';
  var origin = profile.from || '', templates = {}; (D.templates || []).forEach(function (t) { templates[t.name] = true; });
  var source = null; (D.profiles || []).forEach(function (p) { if (origin && p.name.toLowerCase() === origin.toLowerCase()) source = p; });
  var fromHtml = origin && origin.toLowerCase() === profile.name.toLowerCase() ? 'from the shipped ' + esc(origin) + ' template'
    : source ? 'cloned from <a href="/fleet/profiles/' + esc(origin) + '">' + esc(origin) + '</a>'
    : templates[origin] ? 'from the ' + esc(origin) + ' template' : origin ? 'from ' + esc(origin) + ', since deleted' : 'from a blank form';
  var clones = profile.clones || [];
  var clonesHtml = clones.length ? clones.map(function (c) { return '<li><a class="chip" href="/fleet/profiles/' + esc(c) + '">' + esc(c) + '</a></li>'; }).join('') : '<li class="dim">no profile was cloned from it yet</li>';
  var histHtml = history.map(function (h) {
    return '<li><span class="rev">rev ' + h.revision + '</span><span class="dim nw">' + esc(ago(h.updated_at)) + ' · ' + esc(h.author) + '</span><span class="reason">' + (h.reason ? esc(h.reason) : '<span class=dim>no reason given</span>') + '</span></li>';
  }).join('');
  var latest = history[0] || {updated_at: profile.created_at, author: profile.created_by};
  var danger = '<button type="button" class="act del-profile" data-name="' + name + '"' + (n ? ' disabled' : '') + '>Delete this profile</button>'
    + (n ? '<p>Not while ' + plural(n, 'bot') + ' carr' + (n !== 1 ? 'y' : 'ies') + ' it. Retire ' + (n !== 1 ? 'them' : 'it') + ' first; the name is never reused.</p>'
         : '<p>No bot carries it. The name stays taken: profiles are never reused under an old name.</p>');
  var takes = profile.takes_new_work !== false;
  return '<p class="crumbs"><a href="/roles">Roles</a> › ' + name + '</p>'
    + '<div class="reg-head"><h1>' + name + '</h1><div class="right">rev ' + profile.revision + ' · ' + esc(ago(latest.updated_at)) + ' · ' + esc(latest.author) + ' · <a class="addbot-btn needs-me" href="/fleet/profiles/new?from=' + name + '">Clone</a></div></div>'
    + '<p class="role">' + esc(profile.role) + '</p>'
    + '<p class="sub">created ' + esc(ago(profile.created_at)) + ' by ' + esc(profile.created_by) + ' · ' + fromHtml + '. Edited in place: a change reaches the bots that carry it at their next prompt, with no new session. Clone makes a variant under another name.'
    + (takes ? '' : ' <b>Works when mentioned:</b> a new bot of it takes a thread only when its first message mentions it.') + '</p>'
    + '<div class="two"><div><form class="card reg-form" id="profile-edit" style="gap:12px"><h2>What it says</h2>'
    + '<label class="fld"><span>Role <small>· one line: what this bot is</small></span><input name="role" required maxlength="4000" value="' + esc(profile.role) + '" autocomplete="off"></label>'
    + '<label class="fld"><span>Responsibilities <small>· what the bot is for, in a paragraph it can judge work against</small></span><textarea name="responsibilities" rows="4" maxlength="4000">' + esc(profile.responsibilities || '') + '</textarea></label>'
    + '<label class="fld"><span>Focus <small>· what to prefer when several threads are open</small></span><textarea name="focus" rows="4" maxlength="4000">' + esc(profile.focus || '') + '</textarea></label>'
    + '<label class="fld"><span>Avoid <small>· work this bot leaves to others</small></span><textarea name="avoid" rows="3" maxlength="4000">' + esc(profile.avoid || '') + '</textarea></label>'
    + '<label class="fld"><span><input type="checkbox" name="takes_new_work"' + (takes ? ' checked' : '') + '> Takes new work by default <small>· a new bot of it takes new threads that mention nobody. Off: it works only when a thread&#39;s first message mentions it. Used when a bot is added; each bot can be changed on its row.</small></span></label>'
    + '<div><h2 style="margin-bottom:6px">Skills · ' + ownCount + '</h2>' + skillsHtml + '<p class="sub" style="margin:8px 0 0">A skill dropped here leaves its bots\' skill directories at their next prompt; one added arrives there.</p></div>'
    + '<label class="fld"><span>Reason for this change <small>· required, as in a knowledge write; it shows in History</small></span><input name="reason" required maxlength="500" placeholder="what changed, and why" autocomplete="off"></label>'
    + '<div class="row needs-me"><button type="submit" class="addbot-go">Save as rev ' + (profile.revision + 1) + '</button><span class="note">' + (n ? plural(n, 'bot') + ' on it read it at their next prompt.' : 'No bot carries it yet.') + '</span></div>'
    + '<p class="status-line" role="status" aria-live="polite"></p></form>'
    + '<div class="card" id="bots"><h2>Bots with this profile · ' + n + '</h2><p class="sub">A bot reads this profile and its skills at the start of a session, and again at its next prompt after an edit. New session makes one start over.</p>' + botsHtml
    + (n ? '<button type="button" class="act needs-me" id="reset-all" style="margin-top:8px">New session for all ' + plural(n, 'bot') + '</button><p class="status-line" id="reset-note" role="status" aria-live="polite"></p>' : '') + '</div>'
    + '</div><div><div class="card" id="gets"><h2>What a bot gets</h2><p class="sub">The text <code>GET /api/me/profile</code> returns to a bot of this profile.</p><pre class="what">' + esc(profile.text) + '</pre></div>'
    + '<div class="card" id="history"><h2>History · ' + history.length + '</h2><ul class="hist">' + histHtml + '</ul><p class="sub" style="margin:8px 0 0">Every save is a new revision; <code>GET /api/profiles/' + name + '/history</code> has the text of each.</p></div>'
    + '<div class="card" id="lineage"><h2>Lineage</h2><dl><dt>from</dt><dd>' + fromHtml + '</dd><dt>clones</dt><dd><ul class="hist" style="margin:0">' + clonesHtml + '</ul></dd></dl></div>'
    + '<div class="card danger"><h2>Danger</h2>' + danger + '</div></div></div>';
}
function wireProfile(P) {
  var form = document.getElementById('profile-edit');
  form.addEventListener('change', function (ev) {
    if (ev.target.name === 'mandatory' && ev.target.checked) form.querySelector('input[name=skill][value="' + ev.target.value + '"]').checked = true;
    if (ev.target.name === 'skill' && !ev.target.checked) form.querySelector('input[name=mandatory][value="' + ev.target.value + '"]').checked = false;
  });
  form.addEventListener('submit', function (ev) {
    ev.preventDefault(); if (!need()) return;
    var f = form.elements, msg = form.querySelector('.status-line'), mand = {};
    form.querySelectorAll('input[name=mandatory]:checked').forEach(function (c) { mand[c.value] = true; });
    var skills = Array.prototype.map.call(form.querySelectorAll('input[name=skill]:checked'), function (c) { return {name: c.value, mandatory: !!mand[c.value]}; })
      .concat(P.kept);
    msg.className = 'status-line'; msg.textContent = 'Saving…';
    call('PUT', '/api/profiles/' + encodeURIComponent(P.name),
         {role: f.role.value.trim(), responsibilities: f.responsibilities.value.trim(), focus: f.focus.value.trim(), avoid: f.avoid.value.trim(),
          skills: skills, takes_new_work: f.takes_new_work.checked, base_revision: P.revision, reason: f.reason.value.trim()})
    .then(function (d) {
      msg.className = 'status-line ok';
      msg.textContent = 'Saved as rev ' + d.profile.revision + '. ' + (P.bots.length ? P.bots.length + ' bot' + (P.bots.length === 1 ? '' : 's') + ' on it read it at their next prompt.' : 'No bot carries it yet.');
      setTimeout(function () { location.reload(); }, 1200);
    }, function (e) { msg.textContent = e.message; });
  });
  var all = document.getElementById('reset-all');
  if (all) all.addEventListener('click', function () {
    var note = document.getElementById('reset-note');
    if (!need() || !confirm('Give ' + P.bots.map(function (h) { return '@' + h; }).join(', ') + ' a new session? Each reads its profile and skills again (when idle; a stopped bot at its next start).')) return;
    call('POST', '/api/profiles/' + encodeURIComponent(P.name) + '/reset').then(function (d) {
      var n = Object.keys(d.skipped);
      note.className = 'status-line ok';
      note.textContent = d.reset.length + ' asked' + (n.length ? '; skipped: ' + n.map(function (h) { return '@' + h + ' (' + d.skipped[h] + ')'; }).join(', ') : '') + '.';
    }, function (e) { note.className = 'status-line'; note.textContent = e.message; });
  });
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button.reset'); if (!b || !need()) return;
    if (!confirm('Give @' + b.dataset.handle + ' a new session? It reads its profile and skills again and starts fresh (when idle; a stopped bot at its next start).')) return;
    call('POST', '/api/fleet/bots/' + encodeURIComponent(b.dataset.handle) + '/reset')
      .then(function () { b.textContent = 'asked'; b.disabled = true; }, function (e) { alert(e.message); });
  });
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button.del-profile'); if (!b || !need()) return;
    if (!confirm('Delete the profile ' + b.dataset.name + '? The name is never reused.')) return;
    call('DELETE', '/api/profiles/' + encodeURIComponent(b.dataset.name))
      .then(function () { location.href = '/roles'; }, function (e) { alert(e.message); });
  });
}
// r: [GET /api/profiles/<n>, its /history, GET /api/skills, GET /api/fleet, GET /api/profiles, GET /api/profiles/templates]
function composeProfile(r) {
  var profile = r[0].profile, bots = [];
  r[3].hosts.forEach(function (h) { h.bots.forEach(function (b) { if ((b.profile || '').toLowerCase() === profile.name.toLowerCase()) bots.push(Object.assign({}, b, {host: h.name})); }); });
  bots.sort(function (a, b) { return a.handle.toLowerCase() < b.handle.toLowerCase() ? -1 : 1; });
  return {profile: profile, history: r[1].revisions, skills: r[2].skills, bots: bots, profiles: r[4].profiles, templates: r[5].templates};
}
function loadProfile() {
  var root = document.getElementById('reg'), url = '/api/profiles/' + encodeURIComponent(NAME);
  Promise.all([call('GET', url), call('GET', url + '/history'), call('GET', '/api/skills'), call('GET', '/api/fleet'), call('GET', '/api/profiles'), call('GET', '/api/profiles/templates')])
  .then(function (r) {
    var D = composeProfile(r), profile = D.profile, bots = D.bots;
    document.title = 'profile ' + profile.name;
    root.innerHTML = profileHtml(D);
    // a base skill the profile lists has no row here; a save carries it over as it was, not drops it (#426)
    var rowless = {}; D.skills.forEach(function (s) { if (s.base) rowless[s.name] = true; });
    wireProfile({name: profile.name, revision: profile.revision, bots: bots.map(function (b) { return b.handle; }),
                 kept: (profile.skills || []).filter(function (s) { return rowless[s.name]; })});
  }, function (e) { showMissing(root, 'profile', e); });
}
if (typeof document !== 'undefined' && document.getElementById('reg')) loadProfile();
"""


# ------------------------------------------------------------- create form

NEW_PROFILE_JS = REG_COMMON_JS + r"""
// D: {starters, chosen, taken, skills}; built from /api/profiles/templates, /api/profiles, /api/skills
function newProfileHtml(D) {
  var chips = D.starters.map(function (s) {
    return '<button type="button" data-key="' + esc(s.key) + '" aria-pressed="' + (s.key === D.chosen ? 'true' : 'false') + '">' + esc(s.label) + (s.kind === 'template' ? ' <small>template</small>' : '') + '</button>';
  }).join('');
  // a base skill reaches every bot whatever its profile (#347): shown ticked and fixed, never sent
  var skills = D.skills.slice().sort(function (a, b) { return (a.base ? 0 : 1) - (b.base ? 0 : 1); });
  var rows = skills.map(function (s) {
    return '<tr' + (s.base ? ' class=dim' : '') + '><td><input type="checkbox" name="skill" value="' + esc(s.name) + '" aria-label="include ' + esc(s.name) + '"' + (s.base ? ' checked disabled' : '') + '></td>'
      + '<td class="who"><a href="/fleet/skills/' + esc(s.name) + '">' + esc(s.name) + '</a><div class="by desc">' + (s.base ? 'base: every bot has it. ' : '') + esc(s.description) + '</div></td>'
      + '<td data-label="mandatory"><label><input type="checkbox" name="mandatory" value="' + esc(s.name) + '" aria-label="' + esc(s.name) + ' mandatory"' + (s.base ? ' checked disabled' : '') + '> read before any work</label></td></tr>';
  }).join('');
  var skillsHtml = rows ? '<table class="bots pick"><thead><tr><th><span class="sr-only">Include</span></th><th>Skill</th><th>Mandatory</th></tr></thead><tbody>' + rows + '</tbody></table>'
    : '<p class="sub">The registry is empty. A profile can be created without skills and more cannot be added later: add skills first if this profile needs them.</p>';
  return '<p class="crumbs"><a href="/roles">Roles</a> › new profile</p><div class="reg-head"><h1 style="font-family:var(--sans)">Create a profile</h1></div>'
    + '<p class="sub">A profile is a role and the skills that go with it. A human can move a bot to another profile from its row; the profile itself is edited on its page, and a different role is a new profile.</p>'
    + '<div class="two"><div><div class="card"><h2>Start from</h2><div class="starters" id="starters">' + chips + '</div>'
    + '<p class="sub" style="margin:8px 0 0">Three templates ship with the board. Any existing profile can be picked too, which is what Clone does. Switching replaces the fields below; nothing is saved until Create.</p></div>'
    + '<form class="card reg-form" id="profile-new" style="gap:12px"><label class="fld"><span>Name <small>· slug; cannot be changed later, and a name once used is never reused</small></span>'
    + '<input name="name" required pattern="[a-z0-9][a-z0-9_\\-]{1,63}" placeholder="developer-2" autocomplete="off"></label>'
    + '<label class="fld"><span>Role <small>· one line: what this bot is</small></span><input name="role" required maxlength="4000" autocomplete="off"></label>'
    + '<label class="fld"><span>Responsibilities <small>· what the bot is for, in a paragraph it can judge work against</small></span><textarea name="responsibilities" rows="4" maxlength="4000"></textarea></label>'
    + '<label class="fld"><span>Focus <small>· what to prefer when several threads are open</small></span><textarea name="focus" rows="4" maxlength="4000"></textarea></label>'
    + '<label class="fld"><span>Avoid <small>· work this bot leaves to others</small></span><textarea name="avoid" rows="3" maxlength="4000"></textarea></label>'
    + '<label class="fld"><span><input type="checkbox" name="takes_new_work" checked> Takes new work by default <small>· a new bot of it takes new threads that mention nobody. Off: it works only when a thread&#39;s first message mentions it. Used when a bot is added; each bot can be changed on its row. Turn it off for a reviewer or an adviser, such as a security engineer.</small></span></label>'
    + '<div><h2 style="margin-bottom:6px">Skills</h2>' + skillsHtml + '</div>'
    + '<div class="row needs-me"><button type="submit" class="addbot-go" id="create-go">Create</button><span class="note" id="create-note"></span></div>'
    + '<p class="status-line" role="status" aria-live="polite"></p></form></div><div>'
    + '<div class="card"><h2>What a bot will get</h2><pre class="what" id="preview"></pre></div>'
    + '<div class="card" id="starting"><h2>Starting point</h2><p class="sub" id="starting-text" style="margin:0"></p></div></div></div>';
}
function buildStarters(templates, profiles, start) {
  var starters = [{key: '', label: 'Blank', kind: 'blank', data: {name: '', role: '', responsibilities: '', focus: '', avoid: '', skills: [], takes_new_work: true}}]
    .concat(templates.map(function (t) { return {key: 'template:' + t.name, label: t.name, kind: 'template', data: t}; }))
    .concat(profiles.map(function (p) { return {key: p.name, label: p.name, kind: 'profile',
      data: {name: p.name, role: p.role, responsibilities: p.responsibilities, focus: p.focus, avoid: p.avoid, skills: p.skills, takes_new_work: p.takes_new_work}}; }));
  var keys = starters.map(function (s) { return s.key; });
  // ?from= names a profile (Clone) or a template; the default is the developer starter
  var chosen = [start, 'template:' + (start || ''), 'developer', 'template:developer'].filter(function (k) { return k && keys.indexOf(k) >= 0; })[0] || '';
  return {starters: starters, chosen: chosen};
}
function wireNewProfile(D) {
  var form = document.getElementById('profile-new'), current = null;
  function starter(key) { return D.starters.filter(function (s) { return s.key === key; })[0]; }
  function fill(key) {
    var s = starter(key); if (!s) return; current = s;
    document.querySelectorAll('#starters button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.key === key)); });
    form.elements.name.value = s.kind === 'blank' ? '' : (D.taken.indexOf(s.data.name) >= 0 ? s.data.name + '-2' : s.data.name);
    ['role', 'responsibilities', 'focus', 'avoid'].forEach(function (k) { form.elements[k].value = s.data[k] || ''; });
    form.elements.takes_new_work.checked = s.data.takes_new_work !== false;
    var picked = {}; (s.data.skills || []).forEach(function (x) { picked[x.name] = !!x.mandatory; });
    form.querySelectorAll('input[name=skill]:not(:disabled)').forEach(function (c) { c.checked = c.value in picked; });
    form.querySelectorAll('input[name=mandatory]:not(:disabled)').forEach(function (c) { c.checked = !!picked[c.value]; });
    var st = document.getElementById('starting-text');
    st.textContent = s.kind === 'blank' ? 'A blank form. Nothing is recorded as lineage.'
      : s.kind === 'template' ? 'The ' + s.label + ' template that ships with the board. The profile records the template\'s name as what it started from.'
      : 'Everything above started as a copy of ' + s.label + '. The link is kept as lineage on both pages; ' + s.label + ' keeps its bots.';
    render();
  }
  function skills() {
    var mand = {}; form.querySelectorAll('input[name=mandatory]:checked').forEach(function (c) { mand[c.value] = true; });
    return Array.prototype.map.call(form.querySelectorAll('input[name=skill]:checked:not(:disabled)'), function (c) { return {name: c.value, mandatory: !!mand[c.value]}; });
  }
  function render() {
    var f = form.elements, name = f.name.value.trim() || '<name>';
    var lines = ['# Your profile: ' + name, '', '**Role.** ' + f.role.value.trim(), ''];
    [['responsibilities', 'Responsibilities'], ['focus', 'Focus'], ['avoid', 'Avoid']].forEach(function (p) {
      if (f[p[0]].value.trim()) lines.push('## ' + p[1], '', f[p[0]].value.trim(), '');
    });
    lines.push('## Skills', '');
    var sk = skills().concat(Array.prototype.map.call(form.querySelectorAll('input[name=skill]:disabled'), function (c) { return {name: c.value, base: true}; }));
    if (!sk.length) lines.push('This profile brings no skills; the guide and your workspace\'s knowledge base are what you have.');
    else { lines.push('Each skill is a SKILL.md you read with `GET /api/skills/<name>`. Read the mandatory ones now, before any work; the others when a thread calls for what they describe.', '');
      sk.forEach(function (s) { lines.push('- **' + s.name + '**' + (s.base ? ' (mandatory, every bot)' : s.mandatory ? ' (mandatory)' : '')); }); }
    lines.push('', 'Skills change only between sessions: what you read now holds until your next session starts.');
    document.getElementById('preview').textContent = lines.join('\n');
    var n = f.name.value.trim();
    document.getElementById('create-go').textContent = 'Create' + (n ? ' ' + n : '');
    document.getElementById('create-note').textContent = D.taken.indexOf(n) >= 0 ? 'That name is taken: profiles are never renamed, and a deleted one keeps its name.'
      : n ? 'New bots can be given it right away.' + (current && current.kind === 'profile' ? ' ' + current.label + ' is untouched.' : '') : '';
  }
  document.getElementById('starters').addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (b) fill(b.dataset.key);
  });
  form.addEventListener('input', render);
  form.addEventListener('change', function (ev) {
    if (ev.target.name === 'mandatory' && ev.target.checked) form.querySelector('input[name=skill][value="' + ev.target.value + '"]').checked = true;
    if (ev.target.name === 'skill' && !ev.target.checked) form.querySelector('input[name=mandatory][value="' + ev.target.value + '"]').checked = false;
    render();
  });
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    if (!need()) return;
    var msg = form.querySelector('.status-line'), f = form.elements, name = f.name.value.trim().toLowerCase();
    msg.textContent = 'Creating…';
    call('POST', '/api/profiles', {name: name, role: f.role.value.trim(), responsibilities: f.responsibilities.value.trim(),
        focus: f.focus.value.trim(), avoid: f.avoid.value.trim(), skills: skills(), takes_new_work: f.takes_new_work.checked,
        from: current && current.kind !== 'blank' ? current.data.name : null})
    .then(function () { location.href = '/fleet/profiles/' + encodeURIComponent(name); },
          function (e) { msg.textContent = e.status ? e.message : 'could not reach the board'; });
  });
  fill(D.chosen);
}
// r: [GET /api/profiles/templates, GET /api/profiles, GET /api/skills]; start: ?from=
function composeNew(r, start) {
  var b = buildStarters(r[0].templates, r[1].profiles, start || null);
  return {starters: b.starters, chosen: b.chosen, taken: r[1].taken, skills: r[2].skills};
}
function loadNewProfile() {
  var root = document.getElementById('reg');
  Promise.all([call('GET', '/api/profiles/templates'), call('GET', '/api/profiles'), call('GET', '/api/skills')]).then(function (r) {
    var D = composeNew(r, (new URLSearchParams(location.search).get('from') || '').trim().toLowerCase());
    root.innerHTML = newProfileHtml(D);
    wireNewProfile(D);
  }, function (e) { showMissing(root, 'profile page', e); });
}
if (typeof document !== 'undefined' && document.getElementById('reg')) loadNewProfile();
"""


# ----------------------------------------------------------------- the Roles page

ROLES_JS = REG_COMMON_JS + r"""
function skillChip(s) {
  return '<a class="chip" href="/fleet/skills/' + esc(s.name) + '">' + esc(s.name) + (s.mandatory ? ' <span class="star" title="mandatory">★</span>' : '') + '</a>';
}
// profiles: GET /api/profiles; orphans: the handles of bots with no profile (from /api/fleet)
function rolesHtml(profiles, orphans) {
  var rows = profiles.map(function (p) {
    var page = '/fleet/profiles/' + esc(p.name), bots = p.bots || [], all = p.skills || [];
    // the mandatory skills and one more name it; the page lists them all
    var shown = all.filter(function (s) { return s.mandatory; }); if (!shown.length) shown = all.slice(0, 1);
    shown = shown.concat(all.filter(function (s) { return shown.indexOf(s) < 0; }).slice(0, 1));
    var left = all.length - shown.length;
    var chips = shown.map(skillChip).join(' ') || '<span class="dim">none</span>';
    if (left > 0) chips += ' <a class="dim" href="' + page + '">+ ' + left + ' more</a>';
    var who = bots.length ? bots.length + ' · ' + bots.map(function (b) { return '<a href="/a/' + esc(b) + '">' + esc(b) + '</a>'; }).join(', ') : '0 · no bot yet';
    var origin = p.from && p.from.toLowerCase() !== p.name.toLowerCase() ? '<div class="by">cloned from <a href="/fleet/profiles/' + esc(p.from) + '">' + esc(p.from) + '</a></div>' : '';
    var acts = '<a class="act" href="' + page + '">view</a><a class="act" href="/fleet/profiles/new?from=' + esc(p.name) + '">clone</a>'
      + (bots.length ? '' : '<button type="button" class="act del-profile" data-name="' + esc(p.name) + '">delete</button>');
    var spec = p.takes_new_work === false ? ' <span class="chip" title="a new bot of it works only when a thread mentions it">when mentioned</span>' : '';
    return '<tr id="profile-' + esc(p.name) + '"><td class="who"><a href="' + page + '">' + esc(p.name) + '</a>' + spec + '<div class="by desc">' + esc(p.role) + '</div>' + origin + '</td>'
      + '<td data-label="skills"><div class="chips">' + chips + '</div></td><td data-label="bots">' + who + '</td><td class="acts">' + acts + '</td></tr>';
  });
  if (orphans.length) rows.push('<tr id="profile-none"><td class="who"><span class="dim">no role</span><div class="by desc">Bots added before roles. To give one a role, retire it and add a new bot.</div></td>'
    + '<td data-label="skills"><span class="dim">—</span></td><td data-label="bots">' + orphans.length + ' · ' + orphans.map(function (b) { return '<a href="/a/' + esc(b) + '">' + esc(b) + '</a>'; }).join(', ') + '</td><td class="acts"></td></tr>');
  var table = rows.length ? '<table class="bots profiles"><thead><tr><th>Role</th><th>Skills</th><th>Bots</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>' + rows.join('') + '</tbody></table>'
    : '<div class="empty">No profile yet.</div>';
  return '<section class="fleet-section reg" id="fleet-profiles"><div class="sec-head"><h2>Roles · ' + profiles.length + '</h2><a class="addbot-btn needs-me" href="/fleet/profiles/new">+ Create a role</a></div>'
    + '<p class="sub">A role is a profile: what a bot is for, and the skills that go with it. A bot is given one when you add it and keeps it. A role is edited on its page; its bots follow at their next prompt. Clone makes a variant.</p>'
    + table + '</section>';
}
// r: [GET /api/profiles, GET /api/fleet]; the bots with no profile are gathered on the last row so they are not forgotten
function composeRoles(r) {
  var seen = {}, orphans = [];
  r[1].hosts.forEach(function (h) { h.bots.forEach(function (b) { if (!b.profile && !b.retired && !seen[b.handle]) { seen[b.handle] = true; orphans.push(b.handle); } }); });
  orphans.sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : a.toLowerCase() > b.toLowerCase() ? 1 : 0; });
  return {profiles: r[0].profiles, orphans: orphans};
}
function loadRoles() {
  var root = document.getElementById('roles-root');
  Promise.all([call('GET', '/api/profiles'), call('GET', '/api/fleet')]).then(function (r) {
    var D = composeRoles(r);
    root.innerHTML = rolesHtml(D.profiles, D.orphans);
    var count = document.querySelector('.fleet-count'); if (count) count.textContent = D.profiles.length + ' roles';
  }, function (e) { root.innerHTML = '<p class="sub">' + esc(e.message || 'could not load the roles') + '</p>'; });
}
if (typeof document !== 'undefined' && document.getElementById('roles-root')) loadRoles();
"""


def _shell(board_name: str, title: str, here: str, js: str) -> str:
    """The page, empty: the script fills #reg from /api."""
    return shell(board_name, title, f'<style>{FLEET_CSS}{REG_CSS}</style><div class="fleet-page"><div class="reg" id="reg">'
                                    f'<p class="sub">Loading…</p></div></div><script>{js}</script>', here=here)


def skill_page(board_name: str) -> str:
    return _shell(board_name, "skill", "/skills", SKILL_PAGE_JS)


def profile_page(board_name: str) -> str:
    return _shell(board_name, "profile", "/roles", PROFILE_PAGE_JS)


def profile_new_page(board_name: str) -> str:
    return _shell(board_name, "new profile", "/roles", NEW_PROFILE_JS)
