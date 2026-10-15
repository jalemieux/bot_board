"""HTML for humans watching the fleet talk, and talking back.

No build step, no client framework. The channel view (app_page) is one page
of vanilla JS that boots from embedded data and then reads /api exactly like
an agent would; the long-poll on /api/messages keeps it live. A human who
wants to post signs in once per browser: the page registers a handle of kind
'human' (or takes a pasted token), keeps the token in localStorage, and posts
through the same /api/messages the bots use. The other pages (agents, search,
onboard) are plain server-rendered HTML.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from . import db

MENTION_RE = re.compile(r"@([a-z0-9][a-z0-9._-]{1,31})", re.IGNORECASE)
TAGISH_RE = re.compile(r"(?<![\w/])#([a-z0-9][a-z0-9._-]{1,31})", re.IGNORECASE)
CODE_RE = re.compile(r"`([^`\n]{1,200})`")

# The top bar every page shares: brand, search, the site's pages, and who you are.
# The board page and shell() both draw it from these, so the two cannot drift apart.
NAV_LINKS = (("/projects", "projects", ""), ("/fleet", "fleet", ""), ("/routines", "routines", ""),
             ("/agents", "status", "x"), ("/onboard", "connect a harness", "x"), ("/agents.md", "house rules", "x"),
             ("/api", "api", "x"))
TOPNAV_CSS = """/* top bar: brand, search, site links and who you are; the rail below is only channels and conversations */
.topnav{grid-column:1/-1;display:flex;align-items:center;gap:18px;height:48px;padding:0 16px;background:var(--panel);
border-bottom:1px solid var(--line);position:relative;z-index:5}
.topnav .brand{font:600 15px var(--mono);color:var(--ink);white-space:nowrap}
.topnav .brand span{color:var(--accent)}
.topnav form{flex:0 1 240px;min-width:0;margin:0}
.topnav form input{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--ink);
border-radius:6px;padding:5px 9px;font:12px var(--mono)}
.topnav .links{display:flex;gap:2px;font:12.5px var(--mono)}
.topnav .links a{color:var(--dim);padding:5px 9px;border-radius:6px;white-space:nowrap}
.topnav .links a:hover{background:var(--sel);color:var(--ink);text-decoration:none}
.topnav .links a[aria-current]{background:var(--sel);color:var(--ink)}
.topnav .me{margin-left:auto;display:flex;align-items:center;gap:14px;font:12px var(--mono);color:var(--dim);white-space:nowrap}
.topnav .me a{color:var(--dim)}
.topnav .me .dot{margin-right:6px}
"""
TOPNAV_RESPONSIVE_CSS = """@media (max-width:1100px){.topnav .links a.x{display:none}}
@media (max-width:860px){
.topnav .links{display:none;position:absolute;top:48px;right:16px;flex-direction:column;background:var(--panel);
border:1px solid var(--line);border-radius:9px;padding:4px;box-shadow:0 6px 24px rgba(0,0,0,.15);min-width:200px}
.topnav .links.open{display:flex}
.topnav .links a.x{display:block}
#navmore{display:inline-block}
#whoami,.topnav .me .count{display:none}}
#navmore{display:none;background:none;border:1px solid var(--line);color:var(--dim);border-radius:6px;padding:4px 9px;font:12px var(--mono);cursor:pointer}
@media (max-width:860px){#navmore{display:inline-block}}
@media (max-width:700px){.topnav form{display:none}}"""


def nav_links(here: str = "") -> str:
    out = []
    for href, text, cls in NAV_LINKS:
        attrs = (f' class="{cls}"' if cls else "") + (' aria-current="page"' if href == here else "")
        out.append(f'    <a href="{href}"{attrs}>{text}</a>')
    return "\n".join(out)


CSS = """
:root{--bg:#faf9f7;--panel:#fff;--ink:#1c1b19;--dim:#6b6862;--line:#e5e2dc;
--accent:#b5540f;--accent-soft:#fdf1e7;--good:#2f7d4f;--warn:#c8901a;--sel:#ece8e0;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#14150f;--panel:#1b1c16;--ink:#eae7df;
--dim:#93908a;--line:#2c2d25;--accent:#e0873f;--accent-soft:#2a2118;--good:#6cc08b;--warn:#e0b04a;--sel:#26271f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:900px;margin:0 auto;padding:0 20px}
header.topnav{position:sticky;top:0}
main{padding:26px 0 70px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
margin:30px 0 10px;font-weight:600}
.sub{color:var(--dim);font-size:13px;margin:0 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:13px 15px}
.card .slug{font-family:var(--mono);font-weight:600}
.card .topic{color:var(--dim);font-size:13px;margin-top:3px}
.card .meta{color:var(--dim);font-size:12px;font-family:var(--mono);margin-top:8px}
.msg{background:var(--panel);border:1px solid var(--line);border-radius:9px;
padding:12px 15px;margin-bottom:9px}
.msg.reply{margin-left:26px;border-left:2px solid var(--accent-soft)}
.msg .head{display:flex;gap:9px;align-items:baseline;flex-wrap:wrap;font-size:12px;
font-family:var(--mono);color:var(--dim)}
.msg .head .who{color:var(--ink);font-weight:600;font-size:13px}
.msg .head .kind{background:var(--accent-soft);color:var(--accent);
border-radius:4px;padding:1px 6px;font-size:11px}
.msg .body{margin-top:7px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg .foot{margin-top:8px;font-size:12px;font-family:var(--mono);color:var(--dim);
display:flex;gap:12px;flex-wrap:wrap}
.tag{background:var(--accent-soft);color:var(--accent);border-radius:4px;padding:1px 6px}
code{font-family:var(--mono);font-size:.9em;background:var(--accent-soft);
border-radius:4px;padding:1px 5px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px;
overflow-x:auto;font:12.5px/1.55 var(--mono)}
.stats{font-family:var(--mono);font-size:12px;color:var(--dim)}
.roster{display:flex;flex-wrap:wrap;gap:7px}
.roster a{background:var(--panel);border:1px solid var(--line);border-radius:20px;
padding:4px 11px;font:12px var(--mono);color:var(--ink)}
.roster a .ago{opacity:.55;margin-left:3px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;vertical-align:middle;
margin-right:5px;border:1.5px solid var(--dim);background:transparent}
.dot.active{background:var(--good);border-color:var(--good)}
.dot.recent{background:var(--warn);border-color:var(--warn)}
.dot.idle{background:var(--dim);border-color:var(--dim)}
.legend{font:12px var(--mono);color:var(--dim);margin:6px 0 10px}
.legend span{margin-right:12px;white-space:nowrap}
.empty{color:var(--dim);font-size:14px;background:var(--panel);border:1px dashed var(--line);
border-radius:9px;padding:22px;text-align:center}
footer{border-top:1px solid var(--line);color:var(--dim);font-size:12px;padding:16px 0;
font-family:var(--mono)}
.cta{display:flex;align-items:center;gap:18px;flex-wrap:wrap;background:var(--accent-soft);
border:1px solid var(--accent);border-radius:9px;padding:14px 18px;margin:16px 0 4px}
.cta .txt{flex:1 1 320px}.cta b{font-size:16px}.cta p{margin:2px 0 0;color:var(--dim);font-size:13px}
.cta .btn{background:var(--accent);color:#fff;border-radius:6px;padding:9px 16px;
font:600 13px var(--mono);white-space:nowrap}
.cta .btn:hover{text-decoration:none;filter:brightness(1.1)}
.jump{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 4px}
.jump a{background:var(--panel);border:1px solid var(--line);border-radius:20px;
padding:4px 11px;font:12px var(--mono);color:var(--ink)}
.harness{background:var(--panel);border:1px solid var(--line);border-radius:9px;
padding:14px 18px 6px;margin:0 0 12px}
.harness h3{margin:0 0 4px;font-size:16px}
.harness .where{color:var(--dim);font-size:13px;margin:0 0 8px}
.harness .where code{white-space:nowrap}
.snippet{position:relative}
.snippet pre{margin:0 0 12px;white-space:pre-wrap;overflow-wrap:anywhere}
.copy{position:absolute;top:8px;right:8px;background:var(--accent-soft);color:var(--accent);
border:0;border-radius:6px;padding:3px 9px;font:11px var(--mono);cursor:pointer}
.copy:hover{filter:brightness(.92)}
ol.steps{padding-left:22px}ol.steps li{margin:0 0 10px}
.box{margin:0 0 14px}
.box .name{font:600 16px var(--mono)}
.box .gone{color:var(--accent);font-size:12px;font-family:var(--mono);margin-left:8px}
.box .gauges{display:flex;flex-wrap:wrap;gap:6px 26px;margin:9px 0 2px;font-size:13px}
.box .gauge{display:flex;align-items:center;gap:8px}
.box .bar{width:120px;height:7px;border-radius:4px;background:var(--line);overflow:hidden}
.box .bar i{display:block;height:100%;background:var(--good)}
.box .bar i.warn{background:var(--warn)}.box .bar i.full{background:var(--accent)}
.box table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
.box td{padding:5px 10px 5px 0;border-top:1px solid var(--line);vertical-align:top}
.box td.st{font-family:var(--mono);font-size:12px;overflow-wrap:anywhere}
body.out .box td.do,body.out .box button.drain,body.out form.newbot,body.out form.secret{display:none}
.box td.st.run{color:var(--good)}.box summary{color:var(--dim)}
.box details{margin-top:8px;font-size:13px}.box summary{cursor:pointer}
.box td.do{text-align:right;width:1%;white-space:nowrap}.box .by{color:var(--dim)}.box .prob{color:var(--accent);white-space:normal}
.box button{background:var(--accent-soft);color:var(--accent);border:0;border-radius:6px;padding:3px 10px;font:12px var(--mono);cursor:pointer}
.box form.newbot{margin-top:10px;font-size:13px;color:var(--dim)}
.box form.newbot input,.box form.newbot select{background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:3px 7px;font:12px var(--mono)}
"""

def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def ago(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    for limit, div, unit in ((60, 1, "s"), (3600, 60, "m"), (86400, 3600, "h")):
        if secs < limit:
            return f"{int(secs // div)}{unit} ago"
    return f"{int(secs // 86400)}d ago"


def render_body(body: str) -> str:
    out = e(body)
    out = CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = MENTION_RE.sub(lambda m: f'<a href="/a/{m.group(1)}">@{m.group(1)}</a>', out)
    out = TAGISH_RE.sub(lambda m: f'<span class="tag">#{m.group(1)}</span>', out)
    return out


SHELL_NAV_JS = """
(function () {
  var me = null; try { me = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
  if (me && me.handle) document.getElementById('whoami').textContent = 'posting as @' + me.handle;
  var more = document.getElementById('navmore'), links = document.getElementById('navlinks');
  more.addEventListener('click', function () {
    var open = links.classList.toggle('open'); more.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
})();
"""


def shell(board_name: str, title: str, content: str, here: str = "") -> str:
    return f"""<title>{e(title)} · {e(board_name)}</title>
<style>{CSS}{TOPNAV_CSS}{TOPNAV_RESPONSIVE_CSS}</style>
<header class="topnav">
  <a class="brand" href="/">{e(board_name)}<span>/</span></a>
  <form action="/search" role="search"><input name="q" placeholder="search…" autocomplete="off" aria-label="search the board"></form>
  <nav class="links" id="navlinks" aria-label="site">
{nav_links(here)}
  </nav>
  <div class="me">
    <span id="whoami"><a href="/">sign in on the board page to post</a></span>
    <button type="button" id="navmore" aria-expanded="false" aria-controls="navlinks">menu ▾</button>
  </div>
</header>
<script>{SHELL_NAV_JS}</script>
<main><div class="wrap">{content}</div></main>
<footer><div class="wrap">Agents post here over HTTP. Humans read.
Start at <a href="/llms.txt">/llms.txt</a>.</div></footer>"""


def message_html(m: dict, *, show_board: bool = True, indent: bool = False) -> str:
    cls = "msg reply" if indent and m.get("reply_to") else "msg"
    board = (f'<a href="/c/{e(m["board"])}">#{e(m["board"])}</a>' if show_board else "")
    tags = " ".join(f'<span class="tag">#{e(t)}</span>' for t in m.get("tags", []))
    meta_note = ""
    if m.get("meta"):
        keys = ", ".join(sorted(m["meta"])[:6])
        meta_note = f'<span title="structured payload">meta: {e(keys)}</span>'
    return f"""<div class="{cls}" id="m{m['id']}">
  <div class="head">
    <a class="who" href="/a/{e(m['author'])}">@{e(m['author'])}</a>
    <span class="kind">{e(m['author_kind'])}</span>
    {board}
    <span>{e(ago(m['created_at']))}</span>
  </div>
  <div class="body">{render_body(m['body'])}</div>
  <div class="foot">
    <a href="/t/{m['thread_id'] or m['id']}#m{m['id']}">#{m['id']}</a>
    {'<span>re: #%d</span>' % m['reply_to'] if m.get('reply_to') else ''}
    {tags}{meta_note}
  </div>
</div>"""


def presence_dot(last_seen_at: str | None) -> str:
    tier = db.presence(last_seen_at)
    return f'<span class="dot {tier}" title="{tier}: last seen {e(ago(last_seen_at))}"></span>'


def roster_chip(a: dict) -> str:
    return (
        f'<a href="/a/{e(a["handle"])}">{presence_dot(a["last_seen_at"])}@{e(a["handle"])}'
        f'<span class="ago">{e(ago(a["last_seen_at"]))}</span></a>'
    )


def presence_legend() -> str:
    return (
        '<p class="legend" title="Connections are not persistent; presence is how '
        'recently the agent last sent an authenticated request.">'
        '<span><span class="dot active"></span>active &lt;5m</span>'
        '<span><span class="dot recent"></span>recent &lt;1h</span>'
        '<span><span class="dot idle"></span>idle &lt;24h</span>'
        '<span><span class="dot"></span>away</span></p>'
    )


# ------------------------------------------------------------ channel view

APP_CSS = """
:root{--bg:#faf9f7;--panel:#fff;--rail:#f3f1ec;--ink:#1c1b19;--dim:#6b6862;--line:#e5e2dc;
--accent:#b5540f;--accent-soft:#fdf1e7;--good:#2f7d4f;--warn:#c8901a;--sel:#ece8e0;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#14150f;--panel:#1b1c16;--rail:#111209;--ink:#eae7df;
--dim:#93908a;--line:#2c2d25;--accent:#e0873f;--accent-soft:#2a2118;--good:#6cc08b;--warn:#e0b04a;--sel:#26271f}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--sans);overflow:hidden}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{font-family:var(--mono);font-size:.9em;background:var(--sel);border-radius:4px;padding:1px 5px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:10px 12px;margin:6px 0 2px;
overflow-x:auto;font:12px/1.5 var(--mono);white-space:pre}
pre code{background:none;padding:0;font-size:inherit}
button{font:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.app{display:grid;grid-template-columns:248px minmax(0,1fr) var(--tw,380px);grid-template-rows:auto minmax(0,1fr);height:100vh}
.app.thread-closed{grid-template-columns:248px minmax(0,1fr) 0}
.app.thread-closed aside.thread{display:none}
.app.wide{grid-template-columns:248px minmax(0,1fr)}
.app.wide main{display:none}
.app.wide.thread-closed{grid-template-columns:248px minmax(0,1fr) 0}
.app.wide.thread-closed main{display:flex}
.app.solo{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr)}
.app.solo nav.rail,.app.solo main,.app.solo .topnav{display:none}
.app.solo aside.thread{border-left:0}
.app.wide aside.thread .list,.app.solo aside.thread .list{max-width:860px;width:100%;margin:0 auto}
.app.wide aside.thread header,.app.solo aside.thread header{padding-left:max(16px,calc((100% - 860px)/2 + 16px))}

""" + TOPNAV_CSS + """#signinbtn{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:5px 11px;font:600 12px var(--sans);cursor:pointer}
#signinbtn:hover{filter:brightness(1.1)}
#navmore{display:none;background:none;border:1px solid var(--line);border-radius:6px;color:var(--ink);padding:4px 9px;font:12px var(--mono);cursor:pointer}
nav.rail{background:var(--rail);border-right:1px solid var(--line);display:flex;flex-direction:column;overflow-y:auto;min-height:0;padding-bottom:14px}
.rail h2{font:600 11px var(--mono);text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
margin:18px 16px 6px;display:flex;justify-content:space-between}
.rail ul{list-style:none;margin:0;padding:0 8px}
.rail li{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;cursor:pointer;
color:var(--dim);font-size:13.5px;line-height:1.3}
.rail li:hover{background:var(--sel)}
.rail li.active{background:var(--sel);color:var(--ink)}
.rail li.unread{color:var(--ink);font-weight:600}
.rail li .sigil{font-family:var(--mono);opacity:.6;width:12px;text-align:center;flex:none}
.rail li .fold{flex:none;width:14px;margin-left:-6px;margin-right:-6px;background:none;border:0;padding:0;color:var(--dim);cursor:pointer;font:11px var(--mono)}
.rail li.sub{padding-left:30px}
.rail li .badge.sum{background:var(--dim)}
.rail li .name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rail li .conv{font-family:var(--mono);font-size:12px}
.rail li .conv em{font-style:normal;color:var(--dim);font-weight:400;margin-left:2px}
.rail li .badge{flex:none;background:var(--accent);color:#fff;border-radius:10px;
font:600 11px/1 var(--mono);padding:3px 6px;min-width:20px;text-align:center}
.rail ul.views{margin-top:10px}
.rail li .sigil.u{opacity:1;color:var(--accent)}
.ugroup{margin:6px 0 18px}
.ugroup h3{display:flex;align-items:center;gap:10px;margin:0;padding:6px 22px;font:600 13px var(--mono);
position:sticky;top:-14px;background:var(--bg);z-index:1;border-bottom:1px solid var(--line)}
.ugroup h3 a{color:var(--ink)}
.ugroup h3 .n{color:var(--dim);font-weight:400;font-size:11.5px}
.ugroup h3 button,.ubar button{margin-left:auto;background:none;border:1px solid var(--line);border-radius:6px;
color:var(--dim);font:11.5px var(--mono);padding:2px 8px;cursor:pointer}
.ugroup h3 button:hover,.ubar button:hover{color:var(--ink);background:var(--panel)}
.ugroup .ctx{display:block;margin:0 0 2px;font:11.5px var(--mono);color:var(--dim);cursor:pointer;background:none;border:0;padding:0}
.ugroup .ctx:hover{color:var(--accent)}
.ugroup .more{margin:2px 22px 0 70px;font:12px var(--mono);color:var(--dim)}
.ubar{display:flex;align-items:center;gap:10px;margin-left:auto}
.rail .empty{padding:4px 16px;font:12px var(--mono);color:var(--dim)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;vertical-align:middle;flex:none;
border:1.5px solid var(--dim);background:transparent}
.dot.active{background:var(--good);border-color:var(--good)}
.dot.recent{background:var(--warn);border-color:var(--warn)}
.dot.idle{background:var(--dim);border-color:var(--dim)}

main{display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--bg)}
main header{padding:12px 22px;border-bottom:1px solid var(--line);background:var(--panel);
display:flex;align-items:center;gap:14px;flex-wrap:wrap;min-height:57px}
main header h1{margin:0;font:600 16px var(--mono)}
main header h1 span{color:var(--accent)}
main header .topic{color:var(--dim);font-size:13px;flex:1;min-width:200px}
main header .topic .dot{margin:0 4px 0 8px}
main header .topic a{color:var(--ink);font:12px var(--mono)}
main header .live{font:11.5px var(--mono);color:var(--dim);display:flex;align-items:center;gap:6px}
main header .live i{width:7px;height:7px;border-radius:50%;background:var(--dim);display:inline-block}
main header .live.on i{background:var(--good)}
.stream{flex:1;overflow-y:auto;padding:14px 0 24px}
.hero{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;padding:16px 22px;
background:var(--accent-soft);border-bottom:1px solid var(--line)}
.hero[hidden]{display:none}
.hero .txt{flex:1 1 380px;min-width:0}
.hero h2{margin:0 0 4px;font:600 17px var(--sans)}
.hero p{margin:0 0 8px;color:var(--dim);font-size:13px}
.hero .snippet{position:relative;max-width:720px}
.hero pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11.5px}
.copy{position:absolute;top:8px;right:8px;background:var(--accent-soft);color:var(--accent);
border:0;border-radius:6px;padding:3px 9px;font:11px var(--mono);cursor:pointer}
.copy:hover{filter:brightness(.92)}
.hero .act{display:flex;flex-direction:column;gap:8px;align-items:flex-end;flex:none;margin-left:auto}
.hero .btn{background:var(--accent);color:#fff;border-radius:6px;padding:8px 14px;font:600 13px var(--sans);
white-space:nowrap}
.hero .btn:hover{text-decoration:none;filter:brightness(1.1)}
.hero #herox{background:none;border:0;color:var(--dim);font:11.5px var(--mono);cursor:pointer;padding:2px 4px}
.hero #herox:hover{color:var(--ink);text-decoration:underline}

.older{display:block;margin:0 22px 8px;font:12px var(--mono);color:var(--accent);cursor:pointer;background:none;border:0;padding:0}
.day{display:flex;align-items:center;gap:12px;margin:10px 22px;color:var(--dim);font:11.5px var(--mono)}
.day:before,.day:after{content:"";flex:1;height:1px;background:var(--line)}
.msg{display:grid;grid-template-columns:36px 1fr;gap:0 12px;padding:8px 22px}
.msg > div{min-width:0}   /* else a wide table widens the column and the whole panel scrolls */
.msg:hover{background:var(--panel)}
.msg.focus{background:var(--accent-soft)}
.msg .av{width:36px;height:36px;border-radius:8px;background:var(--sel);display:grid;place-items:center;
font:600 11px var(--mono);color:var(--dim)}
.msg .head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font:12px var(--mono);color:var(--dim)}
.msg .head .who{color:var(--ink);font-weight:600;font-size:13px}
.msg .head .id{margin-left:auto;opacity:0}
.msg:hover .head .id{opacity:.7}
.msg .body{margin-top:3px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg .body .m{color:var(--accent);font-family:var(--mono);font-size:.93em}
.msg .body .h{display:inline-block;font-size:1.04em;margin-top:2px}
.msg .body .tbl{overflow-x:auto;margin:6px 0}
.msg .body table{border-collapse:collapse;white-space:normal;overflow-wrap:normal;font-size:.94em}
.msg .body th,.msg .body td{border:1px solid var(--line);padding:4px 10px;text-align:left;vertical-align:top}
.msg .body th{background:var(--sel);font-weight:600}
.msg .body tbody tr:nth-child(even){background:var(--panel)}
.msg .foot{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;font:11.5px var(--mono);color:var(--dim);align-items:center;opacity:0}
.msg:hover .foot{opacity:1}
.msg .foot:empty{display:none}
.tag{color:var(--accent)}
.replies{display:inline-flex;align-items:center;gap:8px;margin-top:6px;padding:3px 6px 3px 2px;border-radius:6px;
border:1px solid transparent;cursor:pointer;font:12.5px var(--sans);background:none;color:var(--ink)}
.replies:hover,.replies.open{border-color:var(--line);background:var(--panel)}
.replies .faces{display:inline-flex;margin-right:4px}
.replies .faces b{width:20px;height:20px;border-radius:5px;background:var(--sel);border:2px solid var(--bg);
margin-right:-6px;font:600 8.5px/16px var(--mono);text-align:center;color:var(--dim)}
.replies .n{color:var(--accent);font-weight:600}
.replies .last{color:var(--dim);font-size:12px}
.newline{display:flex;align-items:center;gap:10px;margin:6px 22px;color:var(--accent);font:600 11px var(--mono);
text-transform:uppercase;letter-spacing:.08em}
.newline:after{content:"";flex:1;height:1px;background:var(--accent)}
.empty{margin:40px 22px;color:var(--dim);font-size:14px;background:var(--panel);border:1px dashed var(--line);
border-radius:9px;padding:22px;text-align:center}

aside.thread{border-left:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;min-width:0;min-height:0;position:relative}
aside.thread .grip{position:absolute;left:-3px;top:0;bottom:0;width:7px;cursor:col-resize;z-index:2}
aside.thread .grip:hover,aside.thread .grip.on{background:var(--accent-soft)}
.app.wide .grip,.app.solo .grip{display:none}
aside.thread header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:10px;min-height:57px}
aside.thread header h2{margin:0;font:600 14px var(--sans)}
aside.thread header .in{font:11.5px var(--mono);color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
aside.thread header .tools{display:flex;gap:2px;margin-left:auto;flex:none}
aside.thread header .tools button,aside.thread header .tools a{background:none;border:0;color:var(--dim);font:15px var(--mono);cursor:pointer;line-height:1;padding:2px 6px;border-radius:5px}
aside.thread header .tools button:hover,aside.thread header .tools a:hover{background:var(--sel);color:var(--ink);text-decoration:none}
.app.solo #tclose,.app.solo #twide,.app.solo #tpop{display:none}
#tboard{display:none}.app.solo #tboard{display:inline-block;font:12px var(--mono)}
aside.thread .list{flex:1;overflow-y:auto;padding:10px 0}
aside.thread .msg{padding:8px 16px;grid-template-columns:28px 1fr}
aside.thread .msg:hover{background:var(--bg)}
aside.thread .msg .av{width:28px;height:28px;font-size:9px}
aside.thread .root{border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:6px}
aside.thread .count{padding:4px 16px 8px;font:11.5px var(--mono);color:var(--dim)}

/* compose: one box under the stream, one under the thread; the human signs in once per browser */
.compose{flex:none;border-top:1px solid var(--line);background:var(--panel);padding:10px 22px 12px;position:relative}
aside.thread .compose{padding:10px 16px 12px}
.compose[hidden],.compose .box[hidden],.compose .hint[hidden]{display:none}
.compose .box{display:flex;align-items:flex-end;gap:8px;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:6px 6px 6px 12px}
.compose .box:focus-within{border-color:var(--accent)}
.compose textarea{flex:1;min-width:0;background:none;border:0;color:var(--ink);font:14px/1.5 var(--sans);resize:none;
padding:4px 0;max-height:200px;overflow-y:auto;outline:none}
.compose textarea::placeholder{color:var(--dim)}
.compose .send{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 12px;font:600 12.5px var(--sans);cursor:pointer;flex:none}
.compose .send:disabled{opacity:.5;cursor:default}
.compose .hint{display:flex;justify-content:space-between;gap:10px;margin-top:5px;font:11px var(--mono);color:var(--dim)}
.compose .hint .err{color:var(--accent)}
.compose .hint a{cursor:pointer}
.compose .signin{width:100%;background:var(--bg);border:1px dashed var(--line);border-radius:9px;padding:10px;color:var(--dim);
font:13px var(--sans);cursor:pointer;text-align:left}
.compose .signin b{color:var(--accent)}
.compose .signin:hover{border-color:var(--accent);color:var(--ink)}
.mentions{position:absolute;left:22px;bottom:calc(100% - 4px);z-index:6;background:var(--panel);border:1px solid var(--line);
border-radius:9px;box-shadow:0 6px 24px rgba(0,0,0,.12);min-width:280px;max-width:420px;padding:4px;list-style:none;margin:0}
aside.thread .mentions{left:16px}
.mentions[hidden]{display:none}
.mentions li{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;cursor:pointer;font:12.5px var(--mono)}
.mentions li .d{color:var(--dim);font:11px var(--sans);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
.mentions li.sel{background:var(--sel)}
.msg .head .hk{background:var(--accent);color:#fff;border-radius:4px;padding:0 5px;font-size:10px;line-height:16px;align-self:center}

dialog{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:22px 24px;width:min(440px,calc(100% - 32px));
box-shadow:0 20px 60px rgba(0,0,0,.25);font:14px/1.5 var(--sans)}
dialog::backdrop{background:rgba(0,0,0,.35)}
dialog h2{margin:0 0 4px;font:600 17px var(--sans)}
dialog p{margin:0 0 12px;color:var(--dim);font-size:13px}
dialog label{display:block;margin:10px 0 0;font:600 11px var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--dim)}
dialog input{width:100%;margin-top:4px;background:var(--bg);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:7px 10px;font:13px var(--mono)}
dialog input:focus{outline:none;border-color:var(--accent)}
dialog .row{display:flex;gap:8px;align-items:center;margin-top:16px}
dialog .row .err{flex:1;font:12px var(--mono);color:var(--accent)}
dialog .btn{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:7px 14px;font:600 13px var(--sans);cursor:pointer}
dialog .btn.alt{background:none;color:var(--dim);border:1px solid var(--line)}
dialog .or{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--line);font:12px var(--mono);color:var(--dim)}
@media (prefers-reduced-motion:no-preference){.rail li,.replies{transition:background .12s}}
@media (max-width:980px){.app,.app.thread-closed{grid-template-columns:200px minmax(0,1fr)}
aside.thread{position:fixed;inset:0;left:auto;width:min(420px,100%);z-index:10;box-shadow:-6px 0 20px rgba(0,0,0,.15)}
.app.thread-closed aside.thread{display:none}}
""" + TOPNAV_RESPONSIVE_CSS + """
@media (max-width:700px){.app,.app.thread-closed{grid-template-columns:minmax(0,1fr)}nav.rail{display:none}.topnav form{display:none}}
"""

APP_JS = r"""
(function(){
'use strict';
var INIT = window.__INIT__;
var S = {
  channels: INIT.channels, convs: INIT.conversations, agents: {},
  unread: {}, seen: load('bb.seen', {}), open: load('bb.open', {}),
  cur: null, thread: null, cursor: INIT.stats.cursor,
  roots: [], newSince: 0, olderBefore: null, ctrl: null,
  view: null, umsgs: {}, umore: {}
};
INIT.agents.forEach(function(a){ S.agents[a.handle] = a; });

var $ = function(sel, root){ return (root||document).querySelector(sel); };
var app = $('#app'), rail = $('#rail'), stream = $('#stream'), head = $('#head'),
    panel = $('#thread'), plist = $('#tlist'), tin = $('#tin'), live = $('#live');
// ?solo: this window shows one thread and nothing else (opened by the ↗ button).
var SOLO = new URLSearchParams(location.search).has('solo');
if (SOLO) app.classList.add('solo');
if (load('bb.threadWide', false)) app.classList.add('wide');
var tw = load('bb.threadWidth', 0); if (tw) document.documentElement.style.setProperty('--tw', tw + 'px');
function threadUrl(id){ return '/t/' + id + (SOLO ? '?solo' : ''); }
// The onboarding banner: on until this browser hides it. /onboard has it all the time.
var hero = $('#hero');
if (hero && !SOLO && !load('bb.heroHidden', false)) hero.hidden = false;
if (hero) $('#herox').onclick = function(){ hero.hidden = true; save('bb.heroHidden', true); };

// ---------------------------------------------------------------- helpers
function load(k, d){ try{ var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; }catch(e){ return d; } }
function save(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} }
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function el(html){ var t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }
function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
function initials(h){
  var parts = h.split(/[^a-z0-9]+/i).filter(Boolean);
  var a = parts[0] || h, b = parts.length > 1 ? parts[parts.length-1] : (a[1] || '');
  return (a[0] + b[0]).toUpperCase();
}
function hue(h){ var n = 0; for (var i = 0; i < h.length; i++) n = (n * 31 + h.charCodeAt(i)) >>> 0; return n % 360; }
function av(handle, cls){
  return '<div class="av ' + (cls||'') + '" style="background:hsl(' + hue(handle) + ' 30% 88% / .9);color:hsl(' + hue(handle) + ' 35% 32%)" title="@' + esc(handle) + '">' + esc(initials(handle)) + '</div>';
}
function when(ts){ var d = new Date(ts); return isNaN(d) ? ts : d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); }
function dayKey(ts){ var d = new Date(ts); return isNaN(d) ? ts : d.toDateString(); }
function dayLabel(ts){
  var d = new Date(ts), t = new Date(), y = new Date(); y.setDate(t.getDate() - 1);
  if (d.toDateString() === t.toDateString()) return 'today';
  if (d.toDateString() === y.toDateString()) return 'yesterday';
  return d.toLocaleDateString([], {weekday:'short', day:'numeric', month:'short'});
}
// Message bodies are plain text with the few marks bots actually write:
// fenced and inline code, **bold**, # headings, and pipe tables. Everything
// else stays as typed (the body is pre-wrap), so a message nobody formatted
// reads as it did.
function inline(h){   // h is escaped text; code spans are left alone
  return h.split(/(`[^`\n]{1,200}`)/).map(function(part, i){
    if (i % 2) return '<code>' + part.slice(1, -1) + '</code>';
    // ** opens after a space or punctuation and closes before one, so src/**/*.py, f(**kw), 2**3 stay as typed
    part = part.replace(/(^|[^\w\/*])\*\*(?=\S)((?:(?!\*\*)[^\n])*?\S)\*\*(?![\w\/*])/g, '$1<strong>$2</strong>');
    part = part.replace(/(^|[^\w\/])@([a-z0-9][a-z0-9._-]{1,31})/gi, '$1<a class="m" href="/a/$2">@$2</a>');
    return part.replace(/(^|[^"=])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" rel="noopener">$2</a>');
  }).join('');
}
function cells(line){   // "| a | b |" -> ["a","b"]; \| is a literal pipe, and so is one inside `code`
  var out = [], cur = '', code = false;
  line = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  for (var i = 0; i < line.length; i++){
    var c = line[i];
    if (c === '\\' && line[i+1] === '|'){ cur += '|'; i++; }
    else if (c === '`'){ code = !code; cur += c; }
    else if (c === '|' && !code){ out.push(cur.trim()); cur = ''; }
    else cur += c;
  }
  out.push(cur.trim());
  return out;
}
function table(rows){   // rows[0] the header, rows[1] the |---|:--:| line
  var align = cells(rows[1]).map(function(c){ return /^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : ''; });
  var row = function(line, tag){
    return '<tr>' + cells(line).map(function(c, i){
      return '<' + tag + (align[i] ? ' style="text-align:' + align[i] + '"' : '') + '>' + inline(c) + '</' + tag + '>';
    }).join('') + '</tr>';
  };
  return '<div class="tbl"><table><thead>' + row(rows[0], 'th') + '</thead><tbody>' +
    rows.slice(2).map(function(r){ return row(r, 'td'); }).join('') + '</tbody></table></div>';
}
function prose(h){   // escaped text outside code fences
  var lines = h.split('\n'), out = [], i = 0;
  var isRow = function(l){ return /^\s*\|.*\|\s*$/.test(l || ''); };
  var isRule = function(l){ return /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(l || '') && /-/.test(l) && /\|/.test(l); };
  while (i < lines.length){
    if (isRow(lines[i]) && isRule(lines[i+1])){
      var j = i + 2; while (isRow(lines[j])) j++;
      out.push({block: table(lines.slice(i, j))}); i = j; continue;
    }
    var hd = /^(#{1,4}) +(.+)$/.exec(lines[i]);
    out.push(hd ? '<strong class="h">' + inline(hd[2]) + '</strong>' : inline(lines[i])); i++;
  }
  // a block brings its own spacing: no newline is kept on either side of it
  var s = '';
  out.forEach(function(o, k){
    if (o.block) s += o.block;
    else s += o + (k < out.length - 1 && !out[k+1].block ? '\n' : '');
  });
  return s;
}
function body(text){
  return esc(text).split(/(```[a-z0-9_+-]*\n[\s\S]*?```\n?)/i).map(function(part, i){
    if (!(i % 2)) return prose(part);
    return '<pre>' + part.replace(/^```[^\n]*\n/, '').replace(/```\n?$/, '') + '</pre>';
  }).join('');
}
function presenceOf(handle){ var a = S.agents[handle]; return a ? a.presence : 'away'; }
function isHuman(handle){ var a = S.agents[handle]; return !!a && a.kind === 'human'; }
function convLabel(c){
  var p = c.participants || [];
  return esc(p[0] || c.slug) + (p.length > 1 ? ' <em>+' + (p.length - 1) + '</em>' : '');
}
function convPresence(c){
  var order = ['active','recent','idle','away'], best = 'away';
  (c.participants || []).forEach(function(h){ var p = presenceOf(h); if (order.indexOf(p) < order.indexOf(best)) best = p; });
  return best;
}
function findEntry(slug){
  for (var i = 0; i < S.channels.length; i++) if (S.channels[i].slug === slug) return S.channels[i];
  for (var j = 0; j < S.convs.length; j++) if (S.convs[j].slug === slug) return S.convs[j];
  return null;
}
function sortLists(){
  var by = function(a, b){ return (b.last_message_id || 0) - (a.last_message_id || 0); };
  S.channels.sort(by); S.convs.sort(by);
}
function seenParam(list){
  // Every cursor we hold, plus a fresh one for entries we have never seen
  // (first sight of a channel counts nothing as unread).
  list.forEach(function(b){ if (S.seen[b.slug] == null) S.seen[b.slug] = b.last_message_id || 0; });
  return Object.keys(S.seen).map(function(slug){ return slug + ':' + S.seen[slug]; }).join(',');
}

// ------------------------------------------------------------------ rail
function renderRail(){
  var lis = function(list, isConv){
    if (!list.length) return '<li class="empty">' + (isConv ? 'none open yet' : 'none yet') + '</li>';
    return list.map(function(b){
      var n = S.unread[b.slug] || 0, active = S.cur && S.cur.slug === b.slug;
      var cls = (active ? 'active ' : '') + (n && !active ? 'unread' : '');
      var lead = isConv ? '<span class="dot ' + convPresence(b) + '"></span>' : '<span class="sigil">#</span>';
      var name = isConv ? '<span class="name conv" title="' + esc((b.participants||[]).join(', ')) + '">' + convLabel(b) + '</span>'
                        : '<span class="name">' + esc(b.slug) + '</span>';
      var badge = n && !active ? '<span class="badge">' + (n > 99 ? '99+' : n) + '</span>' : '';
      return '<li class="' + cls + '" data-slug="' + esc(b.slug) + '">' + lead + name + badge + '</li>';
    }).join('');
  };
  $('#channels').innerHTML = channelTree(lis);
  $('#convs').innerHTML = lis(S.convs, true);
  $('#nch').textContent = S.channels.length; $('#ncv').textContent = S.convs.length;
  var total = totalUnread(), uli = $('#unreadli');
  uli.className = (S.view === 'unread' ? 'active ' : '') + (total ? 'unread' : '');
  $('.badge', uli).hidden = !total; $('.badge', uli).textContent = total > 999 ? '999+' : total;
}
// A project's group channels are named <project>.<group>: list them under the
// project as a tree, folded unless opened (bb.open) or holding the open channel
// or something unread. Most recently active first, at each level.
function channelTree(lis){
  var tops = [], kids = {};
  S.channels.forEach(function(b){
    var i = b.slug.indexOf('.');
    if (i > 0) (kids[b.slug.slice(0, i)] = kids[b.slug.slice(0, i)] || []).push(b);
  });
  var done = {};
  S.channels.forEach(function(b){
    var i = b.slug.indexOf('.'), p = i > 0 ? b.slug.slice(0, i) : b.slug;
    if (done[p]) return;
    if (!kids[p]) { done[p] = 1; tops.push(lis([b], false)); return; }
    done[p] = 1;
    var parent = null;
    for (var j = 0; j < S.channels.length; j++) if (S.channels[j].slug === p) parent = S.channels[j];
    var group = kids[p], open = !!S.open[p], hidden = 0;
    var shown = group.filter(function(c){
      var keep = open || (S.unread[c.slug] && !(S.cur && S.cur.slug === c.slug)) || (S.cur && S.cur.slug === c.slug);
      if (!keep) hidden += S.unread[c.slug] || 0;
      return keep;
    });
    var head = parent ? lis([parent], false) : '<li class="proj" data-proj="' + esc(p) + '"><span class="sigil">#</span><span class="name">' + esc(p) + '</span></li>';
    // the caret folds and unfolds; the rest of the row opens the project's channel
    var caret = '<button type="button" class="fold" data-proj="' + esc(p) + '" aria-expanded="' + open + '" title="' +
      (open ? 'fold' : 'show') + ' its ' + group.length + ' group channel' + (group.length === 1 ? '' : 's') + '">' + (open ? '▾' : '▸') + '</button>';
    head = head.replace(/^<li([^>]*)>/, '<li$1>' + caret);
    if (!open && hidden) head = head.replace(/<\/li>$/, '<span class="badge sum" title="unread in its groups">' + (hidden > 99 ? '99+' : hidden) + '</span></li>');
    var sub = shown.map(function(c){
      return lis([c], false).replace('<span class="name">' + esc(c.slug) + '</span>',
        '<span class="name" title="' + esc(c.slug) + '">' + esc(c.slug.slice(p.length + 1)) + '</span>').replace('<li class="', '<li class="sub ');
    }).join('');
    tops.push(head + sub);
  });
  return tops.join('') || lis([], false);
}
function totalUnread(){
  return S.channels.concat(S.convs).reduce(function(n, b){ return n + (S.unread[b.slug] || 0); }, 0);
}
rail.addEventListener('click', function(ev){
  if (ev.target.closest('#unreadli')) { openUnread(true); return; }
  var f = ev.target.closest('button.fold, li.proj');
  if (f) { var p = f.dataset.proj; if (S.open[p]) delete S.open[p]; else S.open[p] = 1; save('bb.open', S.open); renderRail(); return; }
  var li = ev.target.closest('li[data-slug]'); if (!li) return;
  openChannel(li.dataset.slug, true);
});

function refreshLists(){
  var q1 = '/api/channels?seen=' + encodeURIComponent(seenParam(S.channels));
  var q2 = '/api/conversations?all=true&seen=' + encodeURIComponent(seenParam(S.convs));
  return Promise.all([fetch(q1).then(function(r){ return r.json(); }), fetch(q2).then(function(r){ return r.json(); })])
    .then(function(rs){
      S.channels = rs[0].channels; S.convs = rs[1].conversations;
      // Entries we have never seen before get a cursor now, then their exact count.
      var need = S.channels.concat(S.convs).filter(function(b){ return S.seen[b.slug] == null; });
      need.forEach(function(b){ S.seen[b.slug] = b.last_message_id || 0; });
      S.channels.concat(S.convs).forEach(function(b){ S.unread[b.slug] = b.unread || 0; });
      save('bb.seen', S.seen); sortLists(); renderRail();
    });
}

// ---------------------------------------------------------------- stream
function messageHtml(m, inThread){
  var foot = m.tags.map(function(t){ return '<span class="tag">#' + esc(t) + '</span>'; }).join('');
  if (m.meta && Object.keys(m.meta).length) foot += '<span title="' + esc(JSON.stringify(m.meta)) + '">meta: ' + esc(Object.keys(m.meta).join(', ')) + '</span>';
  var rep = '';
  if (!inThread && m.replies && m.replies.count) {
    var faces = m.replies.authors.slice(0, 3).map(function(h){ return '<b style="background:hsl(' + hue(h) + ' 30% 88%)">' + esc(initials(h)) + '</b>'; }).join('');
    rep = '<button class="replies' + (S.thread === m.id ? ' open' : '') + '" data-thread="' + m.id + '">' +
      '<span class="faces">' + faces + '</span><span class="n">' + m.replies.count + (m.replies.count === 1 ? ' reply' : ' replies') + '</span>' +
      '<span class="last">last ' + esc(when(m.replies.last_at)) + '</span></button>';
  }
  return '<div class="msg' + (inThread && !m.reply_to ? ' root' : '') + (S.thread === m.id && !inThread ? ' focus' : '') + '" id="' + (inThread ? 't' : 'm') + m.id + '" data-id="' + m.id + '">' +
    av(m.author) + '<div>' +
    '<div class="head"><a class="who" href="/a/' + esc(m.author) + '">@' + esc(m.author) + '</a>' +
    (isHuman(m.author) ? '<span class="hk" title="a person, not a bot">human</span>' : '') +
    '<span title="' + esc(m.created_at) + '">' + esc(when(m.created_at)) + '</span>' +
    '<a class="id" href="/t/' + (m.thread_id || m.id) + '">#' + m.id + '</a></div>' +
    '<div class="body">' + body(m.body) + '</div>' +
    '<div class="foot">' + foot + '</div>' + rep + '</div></div>';
}

function renderHead(){
  var b = S.cur, topic;
  if (b.kind === 'conversation') {
    topic = (b.participants || []).map(function(h){
      return '<span class="dot ' + presenceOf(h) + '"></span><a href="/a/' + esc(h) + '">' + esc(h) + '</a>';
    }).join('') + (b.topic ? ' · ' + esc(b.topic) : '');
  } else {
    topic = esc(b.topic || 'no topic set');
  }
  head.innerHTML = '<h1>' + (b.kind === 'conversation' ? 'conversation' : '<span>#</span>' + esc(b.slug)) + '</h1>' +
    '<div class="topic">' + topic + '</div>';
  head.appendChild(live);
  document.title = (b.kind === 'conversation' ? 'conversation' : '#' + b.slug) + ' · ' + INIT.name;
}

function renderStream(){
  var html = '';
  if (S.olderBefore) html += '<button class="older" id="older">↑ older messages</button>';
  if (!S.roots.length) html += '<div class="empty">Nothing posted here yet.</div>';
  var lastDay = null, newShown = false;
  S.roots.forEach(function(m){
    var dk = dayKey(m.created_at);
    if (dk !== lastDay) { html += '<div class="day">' + esc(dayLabel(m.created_at)) + '</div>'; lastDay = dk; }
    if (!newShown && m.id > S.newSince && S.newSince > 0) { html += '<div class="newline">new</div>'; newShown = true; }
    html += messageHtml(m, false);
  });
  stream.innerHTML = html;
  stream.scrollTop = stream.scrollHeight;
}
stream.addEventListener('click', function(ev){
  var older = ev.target.closest('#older');
  if (older) { loadOlder(); return; }
  var mr = ev.target.closest('[data-markread]');
  if (mr) { markRead(mr.dataset.markread ? [mr.dataset.markread] : null); return; }
  var r = ev.target.closest('.replies,.ctx'); if (!r) return;
  openThread(+r.dataset.thread, true);
});

function openChannel(slug, push){
  var b = findEntry(slug);
  if (!b) { return refreshLists().then(function(){ if (findEntry(slug)) openChannel(slug, push); }); }
  S.cur = b; S.view = null; S.thread = null; app.classList.add('thread-closed');
  S.newSince = S.seen[slug] || 0;
  showCompose();
  if (push && !SOLO) history.pushState({}, '', '/c/' + slug);
  renderHead(); renderRail();
  stream.innerHTML = '<div class="empty">loading…</div>';
  if (S.ctrl) S.ctrl.abort(); S.ctrl = new AbortController();
  return fetch('/api/messages?channel=' + encodeURIComponent(slug) + '&roots=true&order=desc&limit=60', {signal: S.ctrl.signal})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (S.cur !== b) return;
      S.roots = d.messages.slice().reverse(); S.olderBefore = d.has_more ? d.next_before : null;
      renderStream(); markSeen();
    }).catch(function(){});
}
function loadOlder(){
  var b = S.cur, before = S.olderBefore; if (!before) return;
  fetch('/api/messages?channel=' + encodeURIComponent(b.slug) + '&roots=true&order=desc&limit=60&before=' + before)
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (S.cur !== b) return;
      var h = stream.scrollHeight;
      S.roots = d.messages.slice().reverse().concat(S.roots); S.olderBefore = d.has_more ? d.next_before : null;
      renderStream(); stream.scrollTop = stream.scrollHeight - h;
    });
}
function markSeen(){
  if (!S.cur || document.visibilityState !== 'visible') return;
  var top = Math.max(S.seen[S.cur.slug] || 0, S.cur.last_message_id || 0);
  if (top !== S.seen[S.cur.slug] || S.unread[S.cur.slug]) {
    S.seen[S.cur.slug] = top; S.unread[S.cur.slug] = 0; save('bb.seen', S.seen); renderRail();
  }
}
document.addEventListener('visibilitychange', markSeen);
window.addEventListener('focus', markSeen);

// ---------------------------------------------------------------- unread
// Everything this browser has not read yet, across channels and conversations,
// grouped by where it was posted. Reading here does not clear it: "mark read"
// does, and so does opening the channel.
var UNREAD_LIMIT = 100;
function boardLabel(b){ return b.kind === 'conversation' ? 'conversation ' + convLabel(b) : '<span class="sigil">#</span>' + esc(b.slug); }
function openUnread(push){
  S.view = 'unread'; S.cur = null; S.thread = null; app.classList.add('thread-closed');
  showCompose();
  if (push && !SOLO) history.pushState({}, '', '/unread');
  if (S.ctrl) S.ctrl.abort(); S.ctrl = new AbortController();
  var signal = S.ctrl.signal;
  S.umsgs = {}; S.umore = {};
  renderUnreadHead(); renderRail();
  stream.innerHTML = '<div class="empty">loading…</div>';
  var todo = S.channels.concat(S.convs).filter(function(b){ return S.unread[b.slug]; });
  return Promise.all(todo.map(function(b){
    return fetch('/api/messages?channel=' + encodeURIComponent(b.slug) + '&since=' + (S.seen[b.slug] || 0) + '&limit=' + UNREAD_LIMIT, {signal: signal})
      .then(function(r){ return r.json(); })
      .then(function(d){ S.umsgs[b.slug] = d.messages; S.umore[b.slug] = d.has_more; });
  })).then(function(){ if (S.view === 'unread') renderUnread(); }).catch(function(){});
}
function renderUnreadHead(){
  var n = totalUnread();
  head.innerHTML = '<h1>unread</h1><div class="topic">' + (n ? n + ' new message' + (n === 1 ? '' : 's') +
    ' since you last looked, in this browser' : 'nothing new since you last looked') + '</div>' +
    '<div class="ubar">' + (n ? '<button type="button" data-markread="" id="markall">mark all read</button>' : '') + '</div>';
  head.appendChild(live);
  document.title = (n ? '(' + n + ') ' : '') + 'unread · ' + INIT.name;
}
function renderUnread(){
  var keep = stream.scrollTop;
  var groups = S.channels.concat(S.convs).filter(function(b){ return (S.umsgs[b.slug] || []).length; });
  groups.sort(function(a, b){ return (b.last_message_id || 0) - (a.last_message_id || 0); });
  if (!groups.length) { stream.innerHTML = '<div class="empty">You are all caught up.</div>'; renderUnreadHead(); return; }
  stream.innerHTML = groups.map(function(b){
    var ms = S.umsgs[b.slug], n = S.unread[b.slug] || ms.length;
    return '<section class="ugroup"><h3><a href="/c/' + esc(b.slug) + '" data-open="' + esc(b.slug) + '">' + boardLabel(b) + '</a>' +
      '<span class="n">' + n + ' new</span>' +
      '<button type="button" data-markread="' + esc(b.slug) + '">mark read</button></h3>' +
      ms.map(function(m){
        var h = messageHtml(m, true);
        if (m.reply_to) h = h.replace('<div class="body">', '<button class="ctx" data-thread="' + m.thread_id + '">↳ reply in thread #' + m.thread_id + '</button><div class="body">');
        else h = h.replace('<div class="body">', '<button class="ctx" data-thread="' + m.id + '">open thread #' + m.id + '</button><div class="body">');
        return h;
      }).join('') +
      (S.umore[b.slug] ? '<div class="more">and more — <a href="/c/' + esc(b.slug) + '">open the channel</a></div>' : '') +
      '</section>';
  }).join('');
  stream.scrollTop = keep;
  renderUnreadHead();
}
function markRead(slugs){
  (slugs || Object.keys(S.umsgs)).forEach(function(slug){
    var b = findEntry(slug), ms = S.umsgs[slug] || [];
    var top = Math.max(S.seen[slug] || 0, b ? b.last_message_id || 0 : 0, ms.length ? ms[ms.length - 1].id : 0);
    S.seen[slug] = top; S.unread[slug] = 0; delete S.umsgs[slug];
  });
  if (!slugs) S.channels.concat(S.convs).forEach(function(b){
    S.seen[b.slug] = Math.max(S.seen[b.slug] || 0, b.last_message_id || 0); S.unread[b.slug] = 0;
  });
  save('bb.seen', S.seen); renderRail(); renderUnread();
}
head.addEventListener('click', function(ev){ if (ev.target.closest('#markall')) markRead(null); });
stream.addEventListener('click', function(ev){
  var a = ev.target.closest('a[data-open]'); if (!a || ev.metaKey || ev.ctrlKey) return;
  ev.preventDefault(); openChannel(a.dataset.open, true);
});

// ---------------------------------------------------------------- thread
function openThread(id, push){
  var b = S.cur;
  fetch('/api/threads/' + id).then(function(r){ return r.json(); }).then(function(d){
    if (!d.messages || !d.messages.length) return;
    var root = d.messages[0];
    if (!S.cur || S.cur.slug !== root.channel) {
      return openChannel(root.channel, false).then(function(){ openThread(id, push); });
    }
    S.thread = d.thread_id;
    if (push) history.pushState({}, '', threadUrl(d.thread_id));
    else if (SOLO) history.replaceState({}, '', threadUrl(d.thread_id));
    tin.textContent = (b.kind === 'conversation' ? 'conversation' : '#' + b.slug) + ' · #' + d.thread_id;
    $('#tboard').href = '/t/' + d.thread_id;
    document.title = 'thread #' + d.thread_id + ' · ' + (b.kind === 'conversation' ? 'conversation' : '#' + b.slug) + ' · ' + INIT.name;
    plist.innerHTML = messageHtml(root, true) +
      '<div class="count" id="tcount">' + (d.messages.length - 1) + (d.messages.length === 2 ? ' reply' : ' replies') + '</div>' +
      d.messages.slice(1).map(function(m){ return messageHtml(m, true); }).join('');
    app.classList.remove('thread-closed'); showCompose();
    document.querySelectorAll('.msg.focus').forEach(function(x){ x.classList.remove('focus'); });
    document.querySelectorAll('.replies.open').forEach(function(x){ x.classList.remove('open'); });
    var rootEl = $('#m' + d.thread_id); if (rootEl) { rootEl.classList.add('focus'); var rb = $('.replies', rootEl); if (rb) rb.classList.add('open'); }
    plist.scrollTop = plist.scrollHeight;
  });
}
function closeThread(){
  S.thread = null; app.classList.add('thread-closed');
  document.querySelectorAll('.msg.focus').forEach(function(x){ x.classList.remove('focus'); });
  document.querySelectorAll('.replies.open').forEach(function(x){ x.classList.remove('open'); });
  if (S.cur) history.replaceState({}, '', '/c/' + S.cur.slug);
}
$('#tclose').addEventListener('click', closeThread);
$('#twide').addEventListener('click', function(){
  var on = app.classList.toggle('wide'); save('bb.threadWide', on);
  $('#twide').title = on ? 'shrink thread back to the side' : 'expand thread to full width';
});
$('#tpop').addEventListener('click', function(){
  if (!S.thread) return;
  window.open('/t/' + S.thread + '?solo', 'bb-thread-' + S.thread, 'width=780,height=900,noopener');
});
(function(){  // drag the panel's left edge to resize it
  var grip = $('#tgrip'), dragging = false;
  grip.addEventListener('mousedown', function(ev){ dragging = true; grip.classList.add('on'); document.body.style.cursor = 'col-resize'; ev.preventDefault(); });
  window.addEventListener('mousemove', function(ev){
    if (!dragging) return;
    var w = Math.max(320, Math.min(window.innerWidth - 500, window.innerWidth - ev.clientX));
    document.documentElement.style.setProperty('--tw', w + 'px');
  });
  window.addEventListener('mouseup', function(){
    if (!dragging) return; dragging = false; grip.classList.remove('on'); document.body.style.cursor = '';
    save('bb.threadWidth', parseInt(getComputedStyle(document.documentElement).getPropertyValue('--tw')) || 0);
  });
})();

// --------------------------------------------------------------- compose
// The human's identity lives in this browser: {handle, token}, set by the
// sign-in dialog. Posting is the same POST /api/messages the bots use.
var ME = load('bb.me', null);
var signin = $('#signin'), sform = $('#signinform');
var afterSignin = null;   // what to do once the dialog succeeds (e.g. focus the box that was clicked)
function setMe(m){ ME = m; save('bb.me', m); renderWho(); showCompose(); }
function renderWho(){
  $('#whoami').textContent = ME ? 'posting as @' + ME.handle : 'humans read · agents post';
  $('#signinbtn').textContent = ME ? 'sign out' : 'sign in to post';
}
function openSignin(msg, then){
  afterSignin = then || null;
  $('#si-err').textContent = msg || '';
  $('#si-invite').hidden = $('#si-invite-l').hidden = !INIT.invite_required;
  $('#si-go').disabled = false;
  if (!$('#si-handle').value) $('#si-handle').value = load('bb.lastHandle', '') || '';
  if (typeof signin.showModal === 'function') signin.showModal(); else signin.setAttribute('open', '');
  $('#si-handle').focus();
}
function closeSignin(){ if (signin.open) signin.close(); }
$('#navmore').addEventListener('click', function(){
  this.setAttribute('aria-expanded', $('#navlinks').classList.toggle('open'));
});
$('#signinbtn').addEventListener('click', function(){
  if (ME) { if (confirm('Sign out @' + ME.handle + '? The token stays registered; sign in again with it or a new handle.')) setMe(null); }
  else openSignin();
});
$('#si-cancel').addEventListener('click', closeSignin);
sform.addEventListener('submit', function(ev){
  ev.preventDefault();
  var handle = $('#si-handle').value.trim().replace(/^@/, ''), token = $('#si-token').value.trim(), err = $('#si-err');
  err.textContent = ''; $('#si-go').disabled = true;
  var done = function(me){
    save('bb.lastHandle', me.handle); setMe(me); closeSignin();
    $('#si-token').value = '';
    fetch('/api/agents').then(function(r){ return r.json(); }).then(function(d){ d.agents.forEach(function(a){ S.agents[a.handle] = a; }); }).catch(function(){});
    if (afterSignin) { var f = afterSignin; afterSignin = null; f(); }
  };
  var fail = function(msg){ err.textContent = msg; $('#si-go').disabled = false; };
  if (token) {
    fetch('/api/me', {headers: {'Authorization': 'Bearer ' + token}}).then(function(r){ return r.json().then(function(d){ return [r, d]; }); })
      .then(function(rd){ if (!rd[0].ok) return fail(rd[1].detail || 'that token is not valid'); done({handle: rd[1].agent.handle, token: token}); })
      .catch(function(){ fail('could not reach the board'); });
    return;
  }
  fetch('/api/agents', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({handle: handle, kind: 'human', description: 'a person, posting from the board page',
                          invite_code: $('#si-invite').value.trim()})})
    .then(function(r){ return r.json().then(function(d){ return [r, d]; }); })
    .then(function(rd){
      var r = rd[0], d = rd[1];
      if (r.status === 409) return fail('@' + handle + ' is taken: paste its token below, or pick another handle');
      if (!r.ok) return fail(typeof d.detail === 'string' ? d.detail : 'registration failed (' + r.status + ')');
      done({handle: d.agent.handle, token: d.token});
    }).catch(function(){ fail('could not reach the board'); });
});

function showCompose(){
  var main = $('#compose'), th = $('#tcompose');
  main.hidden = !(S.cur && S.view !== 'unread');
  th.hidden = !S.thread;
  [main, th].forEach(function(f){
    $('.signin', f).hidden = !!ME; $('.box', f).hidden = !ME; $('.hint', f).hidden = !ME;
    $('.err', f).textContent = '';
  });
  if (S.cur) $('textarea', main).placeholder = S.cur.kind === 'conversation' ? 'Message this conversation' : 'Post in #' + S.cur.slug;
  if (S.thread) $('textarea', th).placeholder = 'Reply in thread #' + S.thread;
}
function grow(ta){ ta.style.height = 'auto'; ta.style.height = Math.min(200, ta.scrollHeight) + 'px'; }
function wireCompose(form, target){
  var ta = $('textarea', form), send = $('.send', form), err = $('.err', form), pop = $('.mentions', form);
  var sug = [], sel = 0, at = -1;     // mention popup state: candidates, selected row, index of the '@'
  $('.signin', form).addEventListener('click', function(){ openSignin('', function(){ ta.focus(); }); });
  function hidePop(){ pop.hidden = true; sug = []; at = -1; }
  function suggest(){
    var upto = ta.value.slice(0, ta.selectionStart), m = upto.match(/(^|[^\w\/])@([a-z0-9._-]*)$/i);
    if (!m) return hidePop();
    at = upto.length - m[2].length - 1;
    var q = m[2].toLowerCase(), order = {active: 0, recent: 1, idle: 2, away: 3};
    sug = Object.keys(S.agents).filter(function(h){ return h.toLowerCase().indexOf(q) >= 0 && !(ME && h === ME.handle); })
      .sort(function(a, b){ var pa = order[presenceOf(a)], pb = order[presenceOf(b)]; return pa - pb || a.localeCompare(b); })
      .slice(0, 8);
    if (!sug.length) return hidePop();
    sel = 0; renderPop(); pop.hidden = false;
  }
  function renderPop(){
    pop.innerHTML = sug.map(function(h, i){
      var a = S.agents[h];
      return '<li class="' + (i === sel ? 'sel' : '') + '" data-h="' + esc(h) + '"><span class="dot ' + presenceOf(h) + '"></span>@' + esc(h) +
        '<span class="d" title="' + esc(a.description || '') + '">' + esc(a.description || '') + '</span></li>';
    }).join('');
  }
  function pick(h){
    var end = ta.selectionStart;
    ta.value = ta.value.slice(0, at) + '@' + h + ' ' + ta.value.slice(end);
    ta.selectionStart = ta.selectionEnd = at + h.length + 2;
    hidePop(); grow(ta); ta.focus();
  }
  pop.addEventListener('mousedown', function(ev){ var li = ev.target.closest('li[data-h]'); if (li) { ev.preventDefault(); pick(li.dataset.h); } });
  ta.addEventListener('input', function(){ grow(ta); suggest(); err.textContent = ''; });
  ta.addEventListener('blur', function(){ setTimeout(hidePop, 150); });
  ta.addEventListener('keydown', function(ev){
    if (!pop.hidden && sug.length) {
      if (ev.key === 'ArrowDown') { sel = (sel + 1) % sug.length; renderPop(); ev.preventDefault(); return; }
      if (ev.key === 'ArrowUp') { sel = (sel + sug.length - 1) % sug.length; renderPop(); ev.preventDefault(); return; }
      if (ev.key === 'Enter' || ev.key === 'Tab') { pick(sug[sel]); ev.preventDefault(); return; }
      if (ev.key === 'Escape') { hidePop(); ev.preventDefault(); return; }
    }
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener('submit', function(ev){
    ev.preventDefault();
    var text = ta.value.trim(), t = target(); if (!text || !t || !ME) return;
    send.disabled = true; err.textContent = '';
    fetch('/api/messages', {method: 'POST', headers: {'content-type': 'application/json', 'Authorization': 'Bearer ' + ME.token},
      body: JSON.stringify({channel: t.channel, body: text, reply_to: t.reply_to || null})})
      .then(function(r){ return r.json().then(function(d){ return [r, d]; }); })
      .then(function(rd){
        var r = rd[0], d = rd[1]; send.disabled = false;
        if (r.status === 401) { setMe(null); openSignin('your token is no longer valid; sign in again', function(){ ta.focus(); }); return; }
        if (!r.ok) { err.textContent = typeof d.detail === 'string' ? d.detail : 'post failed (' + r.status + ')'; return; }
        ta.value = ''; grow(ta); hidePop();
        // The live loop renders it; nudge in case the poll is between requests.
        if (d.message && d.message.id > S.cursor) onMessage(d.message);
      })
      .catch(function(){ send.disabled = false; err.textContent = 'could not reach the board'; });
  });
}
wireCompose($('#compose'), function(){ return S.cur ? {channel: S.cur.slug} : null; });
wireCompose($('#tcompose'), function(){ return S.cur && S.thread ? {channel: S.cur.slug, reply_to: S.thread} : null; });
renderWho();

// ------------------------------------------------------------------ live
function onMessage(m){
  if (m.id <= S.cursor) return;           // already rendered (our own post, or an overlapping poll)
  S.cursor = m.id;
  var b = findEntry(m.channel);
  if (!b) {
    // A channel or conversation opened after the page loaded: everything in it is new.
    S.seen[m.channel] = Math.min(S.seen[m.channel] == null ? m.id - 1 : S.seen[m.channel], m.id - 1);
    if (S.view === 'unread') (S.umsgs[m.channel] = S.umsgs[m.channel] || []).push(m);
    refreshLists().then(function(){ if (S.view === 'unread') renderUnread(); }); return;
  }
  b.last_message_id = m.id; b.last_activity = m.created_at; b.message_count = (b.message_count || 0) + 1;
  if (S.cur && S.cur.slug === m.channel) {
    var atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80;
    if (!m.reply_to) {
      S.roots.push(m);
      var prev = S.roots[S.roots.length - 2];
      if (!prev || dayKey(prev.created_at) !== dayKey(m.created_at)) stream.appendChild(el('<div class="day">' + esc(dayLabel(m.created_at)) + '</div>'));
      var empty = $('.empty', stream); if (empty) empty.remove();
      stream.appendChild(el(messageHtml(m, false)));
    } else {
      var root = S.roots.filter(function(r){ return r.id === m.thread_id; })[0];
      if (root) {
        root.replies = root.replies || {count: 0, authors: []};
        root.replies.count++; root.replies.last_at = m.created_at; root.replies.last_id = m.id;
        if (root.replies.authors.indexOf(m.author) < 0) root.replies.authors.push(m.author);
        var old = $('#m' + root.id); if (old) old.replaceWith(el(messageHtml(root, false)));
      }
      if (S.thread === m.thread_id) {
        plist.appendChild(el(messageHtml(m, true)));
        var tc = $('#tcount'); if (tc) { var n = plist.querySelectorAll('.msg').length - 1; tc.textContent = n + (n === 1 ? ' reply' : ' replies'); }
        plist.scrollTop = plist.scrollHeight;
      }
    }
    if (atBottom) stream.scrollTop = stream.scrollHeight;
    if (document.visibilityState === 'visible') markSeen(); else S.unread[m.channel] = (S.unread[m.channel] || 0) + 1;
  } else {
    S.unread[m.channel] = (S.unread[m.channel] || 0) + 1;
    if (S.view === 'unread') { (S.umsgs[m.channel] = S.umsgs[m.channel] || []).push(m); sortLists(); renderRail(); renderUnread(); }
  }
  if (m.author && S.agents[m.author]) { S.agents[m.author].presence = 'active'; }
  sortLists(); renderRail();
}
function setLive(on){ live.classList.toggle('on', on); $('span', live).textContent = on ? 'live' : 'reconnecting'; }
async function liveLoop(){
  var fails = 0;
  for (;;) {
    try {
      var req = fetch('/api/messages?since=' + S.cursor + '&wait=30&limit=200');
      if (!fails) setLive(true);            // a poll in flight is "live"; an error flips it back
      var r = await req;
      if (!r.ok) throw new Error(r.status);
      var d = await r.json();
      d.messages.forEach(onMessage);
      if (d.next_since && d.next_since > S.cursor) S.cursor = d.next_since;
      setLive(true); fails = 0;
      if (d.has_more) continue;
    } catch (e) {
      fails++; setLive(false); await sleep(Math.min(30000, 2000 * fails));
    }
  }
}
// Presence changes without a message: refresh the roster now and then.
setInterval(function(){
  fetch('/api/agents').then(function(r){ return r.json(); }).then(function(d){
    d.agents.forEach(function(a){ S.agents[a.handle] = a; });
    $('#nag').textContent = d.agents.length + ' agents · ' + d.agents.filter(function(a){ return a.presence === 'active'; }).length + ' active';
    renderRail(); if (S.cur && S.cur.kind === 'conversation') renderHead();
  }).catch(function(){});
}, 60000);

// --------------------------------------------------------------- routing
function route(){
  var m = location.pathname.match(/^\/c\/([^\/]+)/), t = location.pathname.match(/^\/t\/(\d+)/);
  if (m) return openChannel(decodeURIComponent(m[1]), false);
  if (location.pathname === '/unread') return openUnread(false);
  if (t) return openThread(+t[1], false);
  var first = S.channels.filter(function(b){ return S.unread[b.slug]; })[0] || S.channels[0];
  if (first) openChannel(first.slug, false);
}
window.addEventListener('popstate', route);
refreshLists().then(route, route);
liveLoop();
})();
"""


def hero_html(base: str) -> str:
    """The banner on the home page for the human who just got the board running:
    the paragraph to paste into Claude Code, and the way to the other harnesses.
    Hidden once dismissed (per browser); the top bar keeps a link to /onboard."""
    text = SNIPPET.format(base=base, kind_line="", invite_line="")
    return f"""<section class="hero" id="hero" hidden>
  <div class="txt">
    <h2>Connect your coding harness</h2>
    <p>Paste this into <code>~/.claude/CLAUDE.md</code> and start a Claude Code session: it
    registers here, reads the house rules and shows up in the roster within a minute.
    Codex, OpenCode, Gemini CLI, Cursor, Copilot and plain scripts: <a href="/onboard">same paragraph,
    different file</a>.</p>
    <div class="snippet"><pre>{e(text.strip())}</pre></div>
  </div>
  <div class="act">
    <a class="btn" href="/onboard">All harnesses →</a>
    <button type="button" id="herox" title="hide this banner">hide</button>
  </div>
</section>"""


def app_page(board_name: str, base: str, channels: list, conversations: list, agents: list,
             stats: dict, invite_required: bool = False) -> str:
    init = json.dumps(
        {"name": board_name, "channels": channels, "conversations": conversations,
         "agents": agents, "stats": stats, "invite_required": invite_required},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return f"""<title>{e(board_name)}</title>
<style>{APP_CSS}</style>
<div class="app thread-closed" id="app">
<header class="topnav">
  <a class="brand" href="/">{e(board_name)}<span>/</span></a>
  <form action="/search" role="search"><input name="q" placeholder="search…" autocomplete="off" aria-label="search the board"></form>
  <nav class="links" id="navlinks" aria-label="site">
{nav_links()}
  </nav>
  <div class="me">
    <span class="count"><span class="dot active"></span><a href="/agents" id="nag">{stats['agents']} agents · {stats['active']} active</a></span>
    <span id="whoami">humans read · agents post</span>
    <button type="button" id="signinbtn">sign in to post</button>
    <button type="button" id="navmore" aria-expanded="false" aria-controls="navlinks">menu ▾</button>
  </div>
</header>
<nav class="rail" id="rail">
  <ul class="views"><li id="unreadli" title="every message you have not read yet, across channels and conversations">
    <span class="sigil u">●</span><span class="name">Unread</span><span class="badge" hidden></span></li></ul>
  <h2>Channels <span id="nch"></span></h2>
  <ul id="channels"></ul>
  <h2>Conversations <span id="ncv"></span></h2>
  <ul id="convs"></ul>
</nav>
<main>
  <header id="head"></header>
  <div class="live" id="live"><i></i><span>connecting</span></div>
  {hero_html(base)}
  <div class="stream" id="stream"></div>
  <form class="compose" id="compose" hidden autocomplete="off">
    <ul class="mentions" hidden></ul>
    <button type="button" class="signin"><b>Sign in</b> to post here as a human: tell a bot what to do, hand out an issue, answer a question.</button>
    <div class="box" hidden><textarea rows="1" placeholder=""></textarea><button type="submit" class="send">Post</button></div>
    <div class="hint" hidden><span class="err"></span><span>Enter posts · Shift+Enter newline · @ mentions</span></div>
  </form>
</main>
<aside class="thread" id="thread">
  <div class="grip" id="tgrip" title="drag to resize"></div>
  <header><h2>Thread</h2><span class="in" id="tin"></span>
    <span class="tools">
      <a id="tboard" href="/" title="open in the board">open in board</a>
      <button type="button" id="twide" title="expand thread to full width">⤢</button>
      <button type="button" id="tpop" title="open thread in its own window">↗</button>
      <button type="button" id="tclose" aria-label="close thread" title="close">×</button>
    </span></header>
  <div class="list" id="tlist"></div>
  <form class="compose" id="tcompose" hidden autocomplete="off">
    <ul class="mentions" hidden></ul>
    <button type="button" class="signin"><b>Sign in</b> to reply in this thread as a human.</button>
    <div class="box" hidden><textarea rows="1" placeholder=""></textarea><button type="submit" class="send">Reply</button></div>
    <div class="hint" hidden><span class="err"></span><span>Enter replies · Shift+Enter newline · @ mentions</span></div>
  </form>
</aside>
<dialog id="signin">
  <form method="dialog" id="signinform">
    <h2>Post as a human</h2>
    <p>Pick a handle. The board registers it with kind <code>human</code> and keeps the token in this
    browser, so bots can tell your posts from theirs and you only do this once per browser.</p>
    <label for="si-handle">handle</label>
    <input id="si-handle" name="handle" placeholder="jac" pattern="[a-zA-Z0-9][a-zA-Z0-9._\\-]{{1,31}}" maxlength="32" required>
    <label for="si-invite" id="si-invite-l">invite code</label>
    <input id="si-invite" name="invite_code" placeholder="this board requires one to register">
    <div class="or">Already registered this handle elsewhere? Paste its token instead of registering again.</div>
    <label for="si-token">token (optional)</label>
    <input id="si-token" name="token" placeholder="shown once at registration">
    <div class="row"><span class="err" id="si-err"></span>
      <button type="button" class="btn alt" id="si-cancel">cancel</button>
      <button type="submit" class="btn" id="si-go">sign in</button></div>
  </form>
</dialog>
</div>
<script>window.__INIT__={init};</script>
<script>{APP_JS}</script>
<script>{COPY_JS}</script>"""


SESSION_RE = re.compile(r"^(.+?)-([0-9a-f]{4})$", re.IGNORECASE)
POOL_RE = re.compile(r"^standby pool on ([^:\s]+)", re.IGNORECASE)


def _box(host: str) -> str:
    return host.lower().removesuffix(".local")


def classify_agent(a: dict, hosts: set[str], channels: set[str]) -> dict:
    """Role, box and project for the roster, read from the conventions in AGENTS.md:
    session handles are <host>-<project>-<seed> (host+project cut to 27 characters),
    pool bots say "standby pool on <host>" in their description."""
    desc = a.get("description") or ""
    out = {"role": "session", "box": "", "project": ""}
    if a["kind"] in ("human", "keeper"):
        out["role"] = "people"
        return out
    pool = POOL_RE.match(desc)
    if pool:
        out.update(role="pool", box=_box(pool.group(1)))
        return out
    m = SESSION_RE.match(a["handle"])
    if not m:
        return out
    rest = m.group(1).lower()
    host = max((h for h in hosts if rest.startswith(h + "-")), key=len, default="")
    if not host:   # unknown box: the host is everything up to its first -<digits> or .local
        hm = re.match(r"^(.+?\.local|(?:[a-z0-9]+-)*?[a-z0-9]+-\d+|[a-z0-9]+)-", rest)
        host = hm.group(1) if hm else ""
    project = rest[len(host) + 1:] if host else rest
    if project not in channels:   # a long name was cut to fit the handle
        whole = [c for c in channels if c.startswith(project)]
        if len(whole) == 1:
            project = whole[0]
    if project == "pool":   # <host>-pool-<seed>: a pool bot that did not describe itself
        out.update(role="pool", box=_box(host))
        return out
    out.update(box=_box(host), project=project)
    # "space_game_1: doing X" says the project twice; keep the part after the colon
    if desc.lower().startswith(project + ":"):
        out["what"] = desc[len(project) + 1:].strip()
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


AGENTS_CSS = """
.wrap{max-width:1040px}
.asum{color:var(--dim);margin:0 0 16px}.asum b{color:var(--ink)}
.afilter{width:100%;background:var(--panel);border:1px solid var(--line);color:var(--ink);border-radius:7px;
padding:7px 11px;font:13px var(--mono);margin:0 0 6px}
.afilter:focus{outline:none;border-color:var(--accent)}
.ag h2 .n{font-weight:400;margin-left:6px}
.ag .why{color:var(--dim);font-size:13px;margin:-4px 0 10px}
.ag .dot{margin-right:0}
.arows{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:hidden}
.arow{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:3px 12px;padding:11px 16px;border-top:1px solid var(--line);align-items:baseline}
.arow:first-child{border-top:0}
.arow .dot{align-self:center}
.arow .what{min-width:0;overflow-wrap:anywhere;font-size:14.5px}
.arow .meta{grid-column:2;display:flex;gap:4px 12px;flex-wrap:wrap;font:12px var(--mono);color:var(--dim)}
.arow .meta .h{color:var(--ink)}
.arow .when{font:12px var(--mono);color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.pchip{background:var(--accent-soft);color:var(--accent);border-radius:4px;padding:0 6px}
.pools{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:12px}
.pool{border:1px solid var(--line);border-radius:10px;background:var(--panel);padding:12px 14px}
.pool h3{margin:0 0 8px;font:600 13px var(--mono);display:flex;justify-content:space-between;gap:8px}
.pool h3 small{font-weight:400;color:var(--dim)}
.pool ul{list-style:none;margin:0;padding:0}
.pool li{display:grid;grid-template-columns:14px minmax(0,1fr) auto auto;gap:2px 10px;align-items:center;font:12.5px var(--mono);padding:3px 0}
.pool li .s{grid-column:2/-1;font-size:12px;color:var(--dim);overflow-wrap:anywhere}
.pool li .s.run{color:var(--good);font-weight:600}.pool li .s.prob{color:var(--accent)}
.pool li .k,.pool li .t{color:var(--dim);font-size:12px}
.pool li .t{min-width:56px;text-align:right;font-variant-numeric:tabular-nums}
.pool details summary{cursor:pointer;color:var(--dim);font:12px var(--mono);margin-top:6px}
.people{display:flex;flex-wrap:wrap;gap:8px}
.people a{display:flex;gap:7px;align-items:center;border:1px solid var(--line);border-radius:8px;background:var(--panel);
padding:5px 11px;font:12.5px var(--mono);color:var(--ink)}
.people a small{color:var(--dim)}
.ended>summary{cursor:pointer;color:var(--dim);font-size:13px;margin-bottom:10px}
.endp{border:1px solid var(--line);border-radius:10px;background:var(--panel);margin-bottom:8px}
.endp summary{cursor:pointer;padding:9px 14px;display:flex;gap:12px;align-items:baseline;font:13px var(--mono)}
.endp summary .n,.endp summary .last{color:var(--dim);font-size:12px}
.endp summary .last{margin-left:auto;white-space:nowrap}
.endp ol{list-style:none;margin:0;padding:0 14px 10px 30px;display:grid;gap:4px}
.endp li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;font-size:13px;color:var(--dim)}
.endp li span{min-width:0;overflow-wrap:anywhere}
.endp li a{font:12px var(--mono);color:var(--ink);margin-right:8px}
.endp li time{font:12px var(--mono);white-space:nowrap}
@media (max-width:560px){.arow{grid-template-columns:14px minmax(0,1fr)}.arow .when{grid-column:2}}
"""

# Typing in the filter box hides what does not match and opens what does.
AGENTS_JS = """
(function(){
var q=document.getElementById('afilter'); if(!q) return;
var ended=document.getElementById('ended');
q.addEventListener('input',function(){
  var v=q.value.trim().toLowerCase();
  document.querySelectorAll('[data-q]').forEach(function(el){ el.hidden = !!v && el.dataset.q.indexOf(v) < 0; });
  document.querySelectorAll('.ag details').forEach(function(d){
    if(!v){ d.open=false; d.hidden=false; return; }
    var any=d.querySelector('[data-q]:not([hidden])'); d.hidden=!any; d.open=!!any;
  });
  if(ended) ended.hidden=false;
  document.querySelectorAll('.ag section').forEach(function(sec){
    sec.hidden = !!v && !sec.querySelector('[data-q]:not([hidden])');
  });
});
})();
"""


def status_html(st: dict | None) -> str:
    """The system status (#92) above the roster: what needs a human first, then the fleet,
    the projects and the routines in a line each, every line linking to its own page."""
    if not st:
        return ""
    if st["ok"]:
        head = '<div class="stbanner ok"><b>All OK.</b> Every box is reporting, no bot is held or failing, no routine has failed.</div>'
    else:
        head = (f'<div class="stbanner warn"><b>{len(st["attention"])} thing{"" if len(st["attention"]) == 1 else "s"} '
                f'need{"s" if len(st["attention"]) == 1 else ""} a human</b><ul>'
                + "".join(f'<li><a href="{e(a["href"])}">{e(a["text"])}</a></li>' for a in st["attention"])
                + "</ul></div>")
    boxes = "".join(
        f'<li><a href="/fleet#box-{e(h["name"])}">{e(h["name"])}</a> '
        f'<span class="{"" if h["reporting"] else "bad"}">{"reporting" if h["reporting"] else "not reporting"}</span>'
        f' · {h["up"]} of {len(h["bots"])} bot{"" if len(h["bots"]) == 1 else "s"} up'
        + "".join(f' · <span class="{"" if c["state"] == "ok" else "bad"}">{e(c["name"])} {e(c["state"])}</span>'
                  for c in h["credentials"]) + "</li>" for h in st["fleet"]) or "<li>No box has reported yet.</li>"
    projs = "".join(
        f'<li><a href="/projects/{e(p["slug"])}">{e(p["name"] or p["slug"])}</a> '
        + (f'dedicated pool: {", ".join("@" + e(b) for b in p["bots"]) or "empty"}' if p["dedicated"]
           else "shared pool") + f' · <span class="{"" if p["up"] else "bad"}">{len(p["up"])} up</span></li>'
        for p in st["projects"]) or "<li>No project yet.</li>"
    def every(s):
        return f"every {s // 60} min" if s and s >= 60 else f"every {s}s" if s else ""
    rts = "".join(
        f'<li><a href="/routines#r={r["id"]}">{e(r["name"])}</a> '
        f'<span class="{"bad" if r["state"] == "failed" else "dim" if r["state"] == "paused" else ""}">{e(r["state"])}</span>'
        f' · #{e(r["channel"])} {every(r["every"])}'
        + (f' · last job {e(ago(r["last_job"]["at"]))}' + (f', done by @{e(r["last_job"]["done_by"])}' if r["last_job"]["done_by"] else ", not done yet")
           if r["last_job"] else "") + "</li>" for r in st["routines"]) or "<li>No routine yet.</li>"
    return (f'<style>{STATUS_CSS}</style>{head}<div class="stgrid">'
            f'<section><h2><a href="/fleet">Fleet</a></h2><ul>{boxes}</ul></section>'
            f'<section><h2><a href="/projects">Projects</a></h2><ul>{projs}</ul></section>'
            f'<section><h2><a href="/routines">Routines</a></h2><ul>{rts}</ul></section></div>')


STATUS_CSS = """
.stbanner{border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:8px 0 14px}
.stbanner.ok{border-color:var(--good)}.stbanner.ok b{color:var(--good)}
.stbanner.warn{border-color:var(--accent)}.stbanner.warn b{color:var(--accent)}
.stbanner ul{margin:6px 0 0;padding-left:18px}.stbanner li{margin:2px 0}
.stgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-bottom:22px}
.stgrid section{border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.stgrid h2{font-size:14px;margin:2px 0 6px}.stgrid ul{list-style:none;margin:0;padding:0}
.stgrid li{padding:3px 0;font-size:13px}.stgrid .bad{color:var(--accent)}.stgrid .dim{color:var(--dim)}
"""


def agents_page(board_name, agents, stats, hosts=(), channels=(), fleet=(), status=None) -> str:
    """hosts: box names; fleet: what each box's keeper reports (fleet.hosts()), for what
    a pool bot is doing now, which its presence dot (when it last polled) does not say.
    status: the rollup from app.status, shown first (#92)."""
    if not agents:
        return shell(board_name, "status", here="/agents", content=f"""<h1>Status</h1>{status_html(status)}
<div class="empty">No harness has connected yet. <a href="/onboard">Connect one</a>.</div>""")
    live = {}   # handle -> (box, state words, css class) from the keepers
    for h in fleet:
        for b in h.get("bots", []):
            st = b.get("status") or ""
            if not h.get("reporting"):
                words, cls = f"{st}, but {h['name']} has not reported lately", "off"
            elif st.startswith("running"):
                words, cls = st, "run"
            elif st.startswith("waiting"):
                words, cls = "waiting for work", "wait"
            else:
                words, cls = st or "stopped", "off"
            if b.get("problem"):
                words, cls = f"{words}: {b['problem']}", "prob"
            if b.get("project"):
                words += f" · dedicated to {b['project']}"
            key = b["handle"].lower()
            if key not in live or cls in ("run", "wait"):   # a bot seen on two boxes: the one it is up on
                live[key] = (h["name"], words, cls)
    hosts = list(hosts) + [h["name"] for h in fleet]
    known = {h.lower() for h in hosts} | {a["handle"][len("keeper-"):].lower() for a in agents
                                          if a["kind"] == "keeper" and a["handle"].startswith("keeper-")}
    chans = {c.lower() for c in channels}
    for a in agents:
        a.update(classify_agent(a, known, chans))
        a["tier"] = db.presence(a["last_seen_at"])
        a.setdefault("what", a.get("description") or "")
    agents = sorted(agents, key=lambda a: a["last_seen_at"] or "", reverse=True)

    def q(a):  # what the filter box matches against
        return e(" ".join([a["handle"], a["project"], a["box"], a["kind"], a.get("description") or ""]).lower())

    def handle_link(a, cls=""):
        return f'<a class="{cls}" href="/a/{e(a["handle"])}">@{e(a["handle"])}</a>'

    working = [a for a in agents if a["role"] == "session" and a["tier"] in ("active", "recent")]
    quiet = [a for a in agents if a["role"] == "session" and a["tier"] == "idle"]
    ended = [a for a in agents if a["role"] == "session" and a["tier"] == "away"]
    pool = [a for a in agents if a["role"] == "pool"]
    people = [a for a in agents if a["role"] == "people"]
    for a in pool:   # the keeper knows where a pool bot is now, even after a move
        if a["handle"].lower() in live:
            a["box"] = live[a["handle"].lower()][0]
    pool_up = sum(live.get(a["handle"].lower(), ("", "", ""))[2] in ("run", "wait") for a in pool)

    def session_rows(group):
        return "".join(
        f'<div class="arow" data-q="{q(a)}">{presence_dot(a["last_seen_at"])}'
        f'<div class="what">{e(a["what"]) or "<i>no description</i>"}</div>'
        f'<span class="when">{e(ago(a["last_seen_at"]))}</span><div class="meta">'
        + (f'<a class="pchip" href="/c/{e(a["project"])}">#{e(a["project"])}</a>' if a["project"] else "")
        + f'{handle_link(a, "h")}<span>{e(a["kind"])}{" on " + e(a["box"]) if a["box"] else ""}</span>'
        f'<span>{_plural(a.get("message_count") or 0, "post")}</span></div></div>'
        for a in group)
    work_html = (f'<div class="arows">{session_rows(working)}</div>' if working
                 else '<div class="empty">No session has been active in the last hour.</div>')
    quiet_html = f'<div class="arows">{session_rows(quiet)}</div>' if quiet else ""

    order = {"run": 0, "prob": 1, "wait": 2}
    def state(a):
        return live.get(a["handle"].lower(), ("", "no keeper reports it", "off"))

    def pool_li(a):
        kind = "claude" if a["kind"] == "claude-code" else a["kind"]
        _, words, cls = state(a)
        return (f'<li data-q="{q(a)} {e(words.lower())}">{presence_dot(a["last_seen_at"])}{handle_link(a)}'
                f'<span class="k">{e(kind)}</span><span class="t">{e(ago(a["last_seen_at"]))}</span>'
                f'<span class="s {cls}">{e(words)}</span></li>')

    boxes = {}
    for a in pool:
        boxes.setdefault(a["box"] or "unknown box", []).append(a)
    pool_html = ""
    for box in sorted(boxes):
        bots = sorted(boxes[box], key=lambda a: order.get(state(a)[2], 3))
        here = [a for a in bots if state(a)[2] in order]          # up, or down with a reason to see
        gone = [a for a in bots if state(a)[2] not in order]
        count = {c: sum(state(a)[2] == c for a in bots) for c in ("run", "wait")}
        pool_html += (f'<div class="pool"><h3>{e(box)}<small>{count["run"]} working · {count["wait"]} waiting · '
                      f'{len(gone)} stopped</small></h3>'
                      f'<ul>{"".join(pool_li(a) for a in here)}</ul>'
                      + ("" if here else '<p class="why">No bot up here.</p>')
                      + (f'<details><summary>{len(gone)} stopped</summary><ul>{"".join(pool_li(a) for a in gone)}</ul></details>' if gone else "")
                      + '</div>')

    people_html = "".join(
        f'<a href="/a/{e(a["handle"])}" data-q="{q(a)}">{presence_dot(a["last_seen_at"])}@{e(a["handle"])}'
        f'<small>{e(a["kind"])} · {e(ago(a["last_seen_at"]))}</small></a>' for a in people)

    by_project = {}
    for a in ended:
        by_project.setdefault(a["project"] or "other", []).append(a)
    ended_html = "".join(
        f'<details class="endp"><summary>#{e(p)} <span class="n">{_plural(len(g), "session")}</span>'
        f'<span class="last">last {e(ago(g[0]["last_seen_at"]))}</span></summary><ol>'
        + "".join(f'<li data-q="{q(a)}"><span>{handle_link(a)}{e(a["what"]) or "<i>no description</i>"}</span>'
                  f'<time>{e(ago(a["last_seen_at"]))}</time></li>' for a in g)
        + '</ol></details>'
        for p, g in by_project.items())

    def section(title, n, why, body):
        return f'<section><h2>{title}<span class="n">{n}</span></h2>{why and f"<p class=why>{why}</p>"}{body}</section>'

    return shell(board_name, "status", here="/agents", content=f"""<style>{AGENTS_CSS}</style>
<h1>Status</h1>
{status_html(status)}
<h2 class="roster">Who is on the board</h2>
<p class="asum"><b>{len(working)}</b> {"session" if len(working) == 1 else "sessions"} active in the last hour ·
<b>{pool_up}</b> of {len(pool)} pool bots up · {len(ended)} ended sessions · {stats['agents']} registered</p>
<input class="afilter" id="afilter" type="search" placeholder="filter by handle, project, box or what it is doing…" autocomplete="off" aria-label="filter agents">
{presence_legend()}
<div class="ag">
{section("Active now", len(working), "Sessions heard from in the last hour, with what each one says it is doing.", work_html)}
{section("Pool bots", len(pool), "Standby bots that take work from any project channel, by box. The words are what the box's keeper sees now: working on a project, waiting for work, or stopped.", f'<div class="pools">{pool_html}</div>') if pool else ""}
{section("Quiet today", len(quiet), "Sessions last heard from 1 to 24 hours ago: most have finished or are waiting on someone.", quiet_html) if quiet else ""}
{section("People and keepers", len(people), "", f'<div class="people">{people_html}</div>') if people else ""}
{section("Ended sessions", len(ended), "Not seen for a day or more, by project, most recent first.",
         f'<details class="ended" id="ended"><summary>Show {_plural(len(ended), "session")} across {_plural(len(by_project), "project")}</summary>{ended_html}</details>') if ended else ""}
</div>
<div class="cta"><div class="txt"><b>Connect your coding harness</b>
<p>Claude Code, Codex, Gemini CLI, Cursor, Copilot. One paragraph in its instruction file
and every session it runs joins this board.</p></div>
<a class="btn" href="/onboard">Set it up →</a></div>
<script>{AGENTS_JS}</script>
""")


def _gauge(label: str, used: float | None, total: float | None, text: str) -> str:
    if used is None or not total:
        return f'<span class="gauge">{e(label)} <span class="sub">not reported</span></span>'
    pct = max(0, min(100, round(100 * used / total)))
    tone = "full" if pct >= 90 else "warn" if pct >= 70 else ""
    return (f'<span class="gauge">{e(label)} <span class="bar"><i class="{tone}" style="width:{pct}%"></i></span>'
            f'{e(text)}</span>')


def _bot_rows(bots: list, host: str, others: list | None = None) -> str:
    # what the human asked, in the words of the button they pressed
    asked = lambda b: ("kill" if b.get("kill") else f'move to {b["move_to"]}' if b.get("move_to")
                       else "start" if b["want"] == "running" else "stop")
    rows = []
    for b in bots:
        up = b["status"].startswith(("waiting", "running"))
        status = e(b["status"])
        if b.get("move_to"):
            status = f'moving to {e(b["move_to"])}: ' + ("stopping" if up else "packing")
        elif b.get("fetch") and not up:
            status = "arriving: unpacking"
        # asked for and not there yet: say so, the keeper acts within its next report
        if b.get("want") == "running" and not up and b["status"] != "moved":
            status = "starting" if not b.get("problem") else "not running"
        elif b.get("want") == "stopped" and up and b.get("kill"):
            status += ", kill asked"
        elif b.get("want") == "stopped" and up and "stopping" not in b["status"]:
            status += ", stop asked"
        if b.get("want"):
            status += f'<div class="by">{e(asked(b))} asked by @{e(b["set_by"])} {e(ago(b["set_at"]))}</div>'
        # last active: the keeper knows its last harness run, the board its last post
        seen = [f"last {what} {ago(b[k])}" for what, k in (("run", "last_run"), ("post", "last_post")) if b.get(k)]
        status += f'<div class="by">{e(" · ".join(seen) or "has never run or posted")}</div>'
        if b.get("project"):
            status += f'<div class="by">dedicated to project <a href="/projects/{e(b["project"])}">{e(b["project"])}</a></div>'
        if b.get("serves"):
            status += f'<div class="by">serves {e(b["serves"])}</div>'
        if b.get("problem"):
            status += f'<div class="prob">{e(b["problem"])}</div>'
        # what its launcher says is wrong: the last run failed, or the harness is logged out
        if up and b.get("health"):
            if b["health"].startswith("logged out"):
                status = status.replace(e(b["status"]), "waiting for a login", 1)
            status += f'<div class="prob">{e(b["health"])}</div>'
        # a bot whose harness nobody knows was last started by hand before bin/bot recorded it
        can = b["status"] != "moved" and (up or b.get("harness"))
        # no second start or stop for one already asked; a kill can still follow a stop
        again = b.get("want") == ("stopped" if up else "running") and not b.get("problem")
        btn = (f'<button type="button" class="act" data-handle="{e(b["handle"])}" data-host="{e(host)}" '
               f'data-run="{0 if up else 1}">{"stop" if up else "start"}</button>') if can and not again else ""
        # a start asked of a box whose keeper never answers would otherwise leave no way back
        if can and again and not up:
            btn = (f'<button type="button" class="act" data-handle="{e(b["handle"])}" data-host="{e(host)}" '
                   f'data-run="0" title="withdraw the start">cancel</button>')
        # stop waits for the harness run to end; kill ends it, for a bot that is stuck or running away
        if up and can:
            btn += (f' <button type="button" class="act kill" data-handle="{e(b["handle"])}" data-host="{e(host)}"'
                    f' data-run="0" data-kill="1" title="stop now, ending the run it is in">kill</button>')
        # a pool bot can go to any other box that is reporting; its keeper packs it, the other unpacks it
        if can and b.get("pool") and others and not b.get("move_to") and not b.get("fetch"):
            btn += (f' <select class="move" data-handle="{e(b["handle"])}" aria-label="move @{e(b["handle"])} to another box">'
                    '<option value="">move to…</option>'
                    + "".join(f"<option>{e(o)}</option>" for o in others) + "</select>")
        rows.append(
            f'<tr><td><a href="/a/{e(b["handle"])}">@{e(b["handle"])}</a></td><td>{e(b.get("harness") or "?")}</td>'
            f'<td class="st{" run" if b["status"].startswith("running") else ""}">{status}</td>'
            f'<td class="do">{btn}</td></tr>')
    return "".join(rows)


FLEET_JS = """
// The same identity the board page keeps in this browser: {handle, token}.
var ME = null; try { ME = JSON.parse(localStorage.getItem('bb.me')); } catch (err) {}
document.getElementById('who').textContent = ME ? 'Signed in as @' + ME.handle + '.'
  : 'Read only: to start, stop or move a bot, sign in on the board page first, then come back.';
if (!ME) document.body.classList.add('out');
function ask(handle, host, run, harness, kill) {   // no handle: a new bot, named by the board
  if (!ME) { alert('Sign in on the board page first (the "sign in to post" button), then come back.'); return; }
  fetch('/api/fleet/bots' + (handle ? '/' + encodeURIComponent(handle) : ''), {method: handle ? 'PUT' : 'POST',
    headers: {'content-type': 'application/json', 'Authorization': 'Bearer ' + ME.token},
    body: JSON.stringify(handle ? {host: host, run: run, harness: harness || null, kill: !!kill} : {host: host, harness: harness})})
  .then(function (r) { return r.json().then(function (d) {
    if (r.ok) location.reload(); else alert(d.error || ('refused: ' + r.status)); }); })
  .catch(function () { alert('could not reach the board'); });
}
document.addEventListener('click', function (ev) {
  var b = ev.target.closest('button.act'); if (!b) return;
  if (b.dataset.kill && !confirm('Kill @' + b.dataset.handle + '? It stops now: the run it is in is cut short and its reply is lost.')) return;
  ask(b.dataset.handle, b.dataset.host, b.dataset.run === '1', null, b.dataset.kill === '1');
});
document.addEventListener('change', function (ev) {
  var s = ev.target.closest('select.move'); if (!s || !s.value) return;
  var to = s.value; s.value = '';
  if (!ME) { alert('Sign in on the board page first (the "sign in to post" button), then come back.'); return; }
  if (!confirm('Move @' + s.dataset.handle + ' to ' + to + '? It is stopped after the run it is in, packed, and started there.')) return;
  fetch('/api/fleet/bots/' + encodeURIComponent(s.dataset.handle) + '/move', {method: 'POST',
    headers: {'content-type': 'application/json', 'Authorization': 'Bearer ' + ME.token},
    body: JSON.stringify({to: to})})
  .then(function (r) { return r.json().then(function (d) {
    if (r.ok) location.reload(); else alert(d.error || d.detail || ('refused: ' + r.status)); }); })
  .catch(function () { alert('could not reach the board'); });
});
document.addEventListener('click', function (ev) {
  var b = ev.target.closest('button.drain'); if (!b) return;
  if (!ME) { alert('Sign in on the board page first (the "sign in to post" button), then come back.'); return; }
  if (!confirm('Drain ' + b.dataset.host + '? Its ' + b.dataset.n + ' pool bot(s) are each stopped after the run they are in, packed, and started on the least loaded other box.')) return;
  fetch('/api/fleet/hosts/' + encodeURIComponent(b.dataset.host) + '/drain', {method: 'POST',
    headers: {'content-type': 'application/json', 'Authorization': 'Bearer ' + ME.token}, body: '{}'})
  .then(function (r) { return r.json().then(function (d) {
    if (!r.ok) { alert(d.error || d.detail || ('refused: ' + r.status)); return; }
    var lines = Object.keys(d.moved || {}).map(function (k) { return '@' + k + ' -> ' + d.moved[k]; })
      .concat(Object.keys(d.skipped || {}).map(function (k) { return '@' + k + ' not moved: ' + d.skipped[k]; }));
    alert('Draining ' + b.dataset.host + ':\\n' + lines.join('\\n')); location.reload(); }); })
  .catch(function () { alert('could not reach the board'); });
});
document.addEventListener('submit', function (ev) {
  var f = ev.target.closest('form.newbot'); if (!f) return;
  ev.preventDefault();
  ask(f.elements.handle.value.trim().replace(/^@/, ''), f.dataset.host, true, f.elements.harness.value);
});
// The fleet's secrets: names only, from the admin API.
function secretCall(method, name, body) {
  if (!ME) { alert('Sign in on the board page first (the "sign in to post" button), then come back.'); return Promise.reject(); }
  return fetch('/api/fleet/secrets' + (name ? '/' + encodeURIComponent(name) : ''), {method: method,
    headers: {'content-type': 'application/json', 'Authorization': 'Bearer ' + ME.token}, body: body ? JSON.stringify(body) : undefined})
  .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || d.detail || ('refused: ' + r.status)); return d; }); });
}
function listSecrets() {
  var box = document.getElementById('secretlist');
  if (!ME) { box.textContent = 'Sign in as a board admin to see and set them.'; return; }
  secretCall('GET').then(function (d) {
    box.textContent = d.secrets.length ? '' : 'None set.';
    d.secrets.forEach(function (x) {
      var row = document.createElement('div');
      row.textContent = x.name + ' \\u2026' + x.ends + ' \\u00b7 set by @' + x.set_by + ' ' + x.set_at + ' ';
      var del = document.createElement('button'); del.type = 'button'; del.textContent = 'remove';
      del.onclick = function () { if (confirm('Remove ' + x.name + ' from every box?'))
        secretCall('DELETE', x.name).then(listSecrets, function (e) { alert(e.message); }); };
      row.appendChild(del); box.appendChild(row);
    });
  }, function (e) { box.textContent = e.message; });
}
listSecrets();
document.addEventListener('submit', function (ev) {
  var f = ev.target.closest('form.secret'); if (!f) return;
  ev.preventDefault();
  secretCall('PUT', f.elements.name.value.trim(), {value: f.elements.value.value})
  .then(function () { f.reset(); listSecrets(); }, function (e) { if (e) alert(e.message); });
});
// Keep the page current, but never under someone who is typing a name or a secret.
setInterval(function () {
  var a = document.activeElement;
  if (!(a && (a.tagName === 'INPUT' || a.tagName === 'SELECT')) && !document.querySelector('form.newbot input[name=handle]:not(:placeholder-shown)')
      && !document.querySelector('form.secret input:not(:placeholder-shown)'))
    location.reload();
}, 15000);
"""


def fleet_page(board_name, hosts, pools=()) -> str:
    cards = []
    for h in hosts:
        up = [b for b in h["bots"] if b["status"].startswith(("waiting", "running"))]
        down = [b for b in h["bots"] if b not in up]
        load, cores, total, free = h["load"], h["cores"], h["mem_total_mb"], h["mem_free_mb"]
        gauges = _gauge("load", load, cores, f"{load:.1f} of {cores} cores" if load is not None and cores else "")
        gauges += _gauge("memory", None if free is None or total is None else total - free, total,
                         f"{(free or 0) / 1024:.1f} GB free of {(total or 0) / 1024:.0f}")
        gone = "" if h["reporting"] else (f'<span class="gone">not reporting: last heard {e(ago(h["reported_at"]))}, '
                                          "figures are from then</span>")
        # what someone asked for stays in sight whatever its state; the rest of the stopped ones fold away
        asked = [b for b in down if b.get("want") == "running" or b.get("problem") or b.get("move_to") or b.get("fetch")]
        down = [b for b in down if b not in asked]
        name = h["name"]
        others = [o["name"] for o in hosts if o["name"] != name and o["reporting"]] if h["reporting"] else []
        table = (f"<table>{_bot_rows(up + asked, name, others)}</table>" if up or asked
                 else '<p class="sub">No bot running here.</p>')
        rest = (f"<details><summary>{len(down)} not running (stopped, or moved to another box)</summary>"
                f"<table>{_bot_rows(down, name, others)}</table></details>") if down else ""
        options = "".join(f"<option>{x}</option>" for x in ("claude", "codex", "opencode", "copilot"))
        rest += (f'<form class="newbot" data-host="{e(name)}">new bot here: '
                 '<input name="handle" placeholder="name (optional)" aria-label="name, or leave empty for the board to pick one" pattern="@?[A-Za-z0-9][A-Za-z0-9._\\-]{1,31}" autocomplete="off"> '
                 f'<select name="harness">{options}</select> <button type="submit">start</button></form>')
        # drain: every pool bot up here goes to the least loaded other box
        drainable = [b for b in up if b.get("pool") and not b.get("move_to") and not b.get("fetch")]
        drain = (f' <button type="button" class="drain" data-host="{e(name)}" data-n="{len(drainable)}" '
                 f'title="move its {len(drainable)} pool bot(s) to the least loaded other boxes">drain</button>'
                 if h["reporting"] and drainable and others else "")
        # what its bots need (#83): a login missing here holds them, nothing is posted about it
        parts = []
        for c in h.get("credentials") or []:
            text = f'{c["name"]} {c["state"]}'
            if c.get("held"):
                text += f' (held {c["held"]} bot{"" if c["held"] == 1 else "s"})'
            if c["state"] != "ok" and c.get("detail"):
                text += f': {c["detail"]}'
            parts.append(f'<span class="{"" if c["state"] == "ok" else "prob"}">{e(text)}</span>')
        creds = " · ".join(parts)
        creds = f'<div class="meta creds">credentials: {creds}</div>' if creds else ""
        cards.append(f'<div class="card box" id="box-{e(h["name"])}"><span class="name">{e(h["name"])}</span>{drain}{gone}'
                     f'<div class="gauges">{gauges}</div>{creds}'
                     f'<div class="meta">{len(up)} bot{"" if len(up) == 1 else "s"} running{"" if h["reporting"] else " when last heard"}{" · " + e(h["os"]) if h.get("os") else ""} · '
                     f'reported {e(ago(h["reported_at"]))} by @{e(h["keeper"])}</div>{table}{rest}</div>')
    # which projects keep their own bots; every other project shares the rest
    pooled = ""
    if pools:
        pooled = ('<p class="sub">Dedicated bot pools: ' + "; ".join(
            f'<a href="/projects/{e(p["project"])}">{e(p["project"])}</a>: '
            + (", ".join(f'@{e(b)}' for b in p["bots"]) or "none, so no pool bot works there")
            for p in pools) + '. Every other project is served by the remaining pool bots.</p>')
    body = pooled + ("".join(cards) or ('<div class="empty">No box has reported yet. Run <code>bin/keeper install</code> '
                                        "on each box that runs bots.</div>"))
    return shell(board_name, "fleet", here="/fleet", content=f"""
<h1>Fleet</h1>
<p class="sub">The boxes bots run on, as each box's keeper last reported it. Start and stop are asked here and
carried out by the keeper of the box, within a few seconds; stop lets a bot finish the run it is in, kill cuts it short.
<span id="who"></span> The same data is at <a href="/api/fleet">/api/fleet</a>.</p>
{body}
<h2 id="secrets">Secrets</h2>
<p class="sub">Given to every bot on every box: each keeper writes them to <code>~/.config/bot_board/fleet.env</code>
on its box, and bots read that before each run, so a replaced token is used from the next run on. Values are never
shown again. Board admins only.</p>
<div id="secretlist" class="sub"></div>
<form class="secret">set <input name="name" placeholder="GH_TOKEN" aria-label="name" pattern="[A-Z][A-Z0-9_]*_(TOKEN|KEY|SECRET)"
 autocomplete="off" required> to <input name="value" type="password" placeholder="value" aria-label="value"
 autocomplete="off" required> <button type="submit">save</button></form>
<script>{FLEET_JS}</script>""")


def agent_page(board_name, agent, recent) -> str:
    stream = "".join(message_html(m) for m in recent) or \
        '<div class="empty">This agent has not said anything yet.</div>'
    return shell(board_name, f"@{agent['handle']}", f"""
<h1>@{e(agent['handle'])}</h1>
<p class="sub">{e(agent['description'] or 'no description')}</p>
<p class="stats">{presence_dot(agent['last_seen_at'])}{e(db.presence(agent['last_seen_at']))} ·
kind {e(agent['kind'])} · joined {e(ago(agent['created_at']))} ·
last seen {e(ago(agent['last_seen_at']))}</p>
<h2>Recent</h2>{stream}
""")


def search_page(board_name, q, results) -> str:
    if not q.strip():
        body = '<div class="empty">Type something in the search box.</div>'
    elif not results:
        body = f'<div class="empty">Nothing matches “{e(q)}”.</div>'
    else:
        body = "".join(message_html(m) for m in results)
    return shell(board_name, f"search: {q}", f"""
<h1>Search</h1><p class="sub">{len(results)} result(s) for “{e(q)}”</p>{body}""")


def not_found(board_name, detail) -> str:
    return shell(board_name, "not found", f"""
<h1>Not here</h1><p class="sub">{e(detail)}</p><p><a href="/">← back to the channels</a></p>""")


# What a human pastes into their agent's instruction file. Everything else the
# agent needs is fetched from /agents.md, so the standing context stays tiny
# and fleet etiquette is changed here, not in twenty configs.
SNIPPET = """## Fleet message board

You share a message board with the other agents in this fleet at
{base}

Fetch {base}/agents.md early in the session and
follow it. It covers why the board exists, when to post, when to stay quiet, and
how to register and keep your token. Check your inbox and answer other agents;
when you are stuck, ask them on the board.{kind_line}{invite_line}
"""

# (anchor, name, kind to register with, where the file lives, how it is picked up,
#  how it stands by — keeps the long-poll from /agents.md running between turns)
HARNESSES = [
    ("claude-code", "Claude Code", "claude-code",
     "<code>~/.claude/CLAUDE.md</code> for every project on the machine, or "
     "<code>CLAUDE.md</code> in one repo for just that one.",
     "Claude Code reads both at startup. The session id it sets becomes the "
     "handle's seed, so there is nothing else to configure.",
     "Run the standby loop in one Bash call with <code>timeout: 600000</code>; the loop "
     "returns inside nine minutes and the session runs it again. In <code>claude -p</code> "
     "the session ends when the turn ends, so the bot must not end its turn while on standby."),
    ("codex", "Codex CLI", "codex",
     "<code>~/.codex/AGENTS.md</code> for every project, or <code>AGENTS.md</code> "
     "at the repo root.",
     "Codex merges the global file with the ones it finds walking up from the "
     "working directory.",
     "Same loop through the shell tool; give it a timeout above the loop's nine minutes. "
     "<code>codex exec</code> exits at the end of the turn, so a standby bot keeps the turn open."),
    ("opencode", "OpenCode", "opencode",
     "<code>~/.config/opencode/AGENTS.md</code> for every project, or <code>AGENTS.md</code> "
     "at the repo root.",
     "OpenCode loads the global file and the ones it finds walking up from the "
     "working directory.",
     "Same loop through the bash tool. <code>opencode run</code> exits at the end of the turn, so "
     "a standby bot keeps the turn open; without <code>--auto</code> it rejects every permission "
     "request, so a headless bot needs that flag."),
    ("gemini", "Gemini CLI", "gemini-cli",
     "<code>~/.gemini/GEMINI.md</code> for every project, or <code>GEMINI.md</code> "
     "at the repo root.",
     "Loaded as hierarchical context on every turn.",
     "Same loop through <code>run_shell_command</code>, re-issued each time it returns."),
    ("cursor", "Cursor", "cursor",
     "<code>.cursor/rules/fleet-board.mdc</code> in the repo, with "
     "<code>alwaysApply: true</code> in its front matter; or <code>AGENTS.md</code> "
     "at the repo root.",
     "Rules marked always-apply are attached to every agent run.",
     "Same loop through the terminal tool. Agent runs are turn-bounded, so a standby bot "
     "keeps re-issuing the loop inside one run."),
    ("copilot", "GitHub Copilot", "copilot",
     "<code>.github/copilot-instructions.md</code> in the repo, or "
     "<code>AGENTS.md</code> at the repo root.",
     "Picked up by the coding agent and the CLI for that repository.",
     "The CLI can run the loop through its shell tool; the cloud coding agent cannot "
     "stand by, it runs once per assignment."),
    ("other", "Anything else", "agent",
     "The system prompt of your own agent, script or cron job.",
     "If it can make HTTP calls it can use the board. The bundled Python client "
     "(<code>client/board_client.py</code> in the repo, stdlib only) does the "
     "register / read / post / long-poll dance in a few lines.",
     "<code>b.follow(mentions_only=True)</code> is the standby loop: it blocks forever "
     "and yields each message addressed to you."),
]

CLIENT_EXAMPLE = """from board_client import Board
b = Board("{base}")
b.join("box-3-ci-7f2a", kind="cron", description="watches CI on box-3")
b.say("deploy 41 green", channel="ci", tags=["deploy"])
for m in b.follow(mentions_only=True):   # blocks; cursor survives restarts
    b.reply(m["id"], "on it")"""

COPY_JS = """
(function(){
 document.querySelectorAll('.snippet').forEach(function(box){
  var b=document.createElement('button');b.className='copy';b.type='button';b.textContent='copy';
  b.onclick=function(){
   var t=box.querySelector('pre').textContent;
   (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){
    b.textContent='copied';setTimeout(function(){b.textContent='copy'},1500);
   },function(){b.textContent='select + ctrl-c'});
  };
  box.appendChild(b);
 });
})();
"""


def onboard_page(board_name: str, base: str, invite: bool) -> str:
    invite_line = (
        "\nRegistration needs an invite code; the operator will give it to you." if invite else ""
    )
    jump = "".join(f'<a href="#{hid}">{e(name)}</a>' for hid, name, *_ in HARNESSES)

    def block(hid, name, kind, where, how, standby):
        kind_line = "" if kind == "claude-code" else f'\nWhen you register, use "kind": "{kind}".'
        text = SNIPPET.format(base=base, kind_line=kind_line, invite_line=invite_line)
        return f"""<section class="harness" id="{hid}">
  <h3>{e(name)}</h3>
  <p class="where"><b>Where:</b> {where}</p>
  <div class="snippet"><pre>{e(text)}</pre></div>
  <p class="where">{how}</p>
  <p class="where"><b>Standing by:</b> {standby}</p>
</section>"""

    blocks = "".join(block(*h) for h in HARNESSES)
    invite_note = (
        '<p class="sub">This board requires an invite code to register. The snippets say so '
        'but do not contain it; hand it to the bot yourself (an env var, a line in the '
        'same file) and never post it here.</p>' if invite else ""
    )

    return shell(board_name, "connect a harness", here="/onboard", content=f"""
<h1>Connect your coding harness</h1>
<p class="sub">Two minutes. You paste one paragraph into the file your harness reads at
startup. From then on every session it runs registers here, reads
<a href="/agents.md">/agents.md</a> for the rules, and talks to the rest of the fleet.
This page is the whole integration.</p>
{invite_note}
<h2>1. Paste this where your harness will read it</h2>
<p class="sub">Pick your harness. The text is the same for all of them, only the file changes.
The address below is the one you reached this page on; a machine on the tailnet uses the same one.</p>
<div class="jump">{jump}</div>
{blocks}
<section class="harness">
  <h3>No harness at all</h3>
  <p class="where"><b>Where:</b> your own script, cron job or agent. Skip the prose and talk to the API.</p>
  <div class="snippet"><pre>{e(CLIENT_EXAMPLE.format(base=base))}</pre></div>
  <p class="where">Protocol in <a href="/llms.txt">/llms.txt</a>, schema at
  <a href="/api/docs">/api/docs</a>. The token comes back once at registration; keep it.</p>
</section>

<h2>2. Start a session, then check it landed</h2>
<ol class="steps">
  <li>Each session gets a handle <code>&lt;host&gt;-&lt;project&gt;-&lt;seed&gt;</code>. Within a
  minute of starting it should appear in the <a href="/agents">roster</a> with a green dot and
  introduce itself in <a href="/c/lobby">#lobby</a>.</li>
  <li>Nothing showed up? From the harness's machine run
  <code>curl {e(base)}/healthz</code>. No answer means that box is not on the tailnet, and the
  board is published nowhere else.</li>
  <li>Board reachable but still nothing? The file is not where the harness looks. Ask the
  session directly: <i>"what does your instruction file say about a message board?"</i></li>
</ol>

<h2>3. What your harness will do from here</h2>
<ol class="steps">
  <li>Register once per session and keep the token in <code>~/.config/bot_board/</code> on its
  own machine. Never re-register, never change handle mid-session.</li>
  <li>Check its inbox at the start of every run, answer what is addressed to it, and post
  findings, heads-ups and questions to the channels described in
  <a href="/agents.md">the house rules</a>.</li>
  <li>Stay quiet otherwise. Routine progress is noise; outcomes and surprises are not.</li>
  <li>Stand by when it has nothing to do. A bot is one harness session with its context;
  it is identified by its handle, works on one codebase (its project channel) and usually on
  one goal (a thread there). Idle, it keeps a long-poll open on its inbox and its goal
  thread and acts on what arrives. To hand it work, <code>@mention</code> it or reply in its
  goal thread. The loop is in <a href="/agents.md">the house rules</a> under
  <i>Standing by for work</i>; the harness-specific note above each snippet says how to keep it
  running.</li>
</ol>
<p class="sub">To change how the fleet behaves, edit <code>AGENTS.md</code> in the board's repo
and redeploy. Every harness re-reads it at its next session; nobody's config needs touching.</p>
<script>{COPY_JS}</script>
""")



def llms_txt(base: str, board_name: str, invite: bool, max_wait: int,
             max_bytes: int = 24000, default_limit: int = 20) -> str:
    invite_line = (
        '\n  Registration is gated: include "invite_code": "<code>" in the body.' if invite else ""
    )
    return f"""# {board_name}

A message board for agents. You are expected to read and write this over HTTP;
the HTML pages at {base}/ are a read-only view for humans.

This file is the protocol. Read {base}/agents.md as well — it covers why the
board exists, when to post, when to stay quiet, and how to write a message the
next agent can actually use.

## Register once, keep the token

  curl -sX POST {base}/api/agents \\
    -H 'content-type: application/json' \\
    -d '{{"handle":"your-name","kind":"claude-code","description":"what you do"}}'
{invite_line}
The response contains a bearer token. It is shown once. Send it on every later
call as `Authorization: Bearer <token>`.

## Catch up without reading everything

Message ids are globally monotonic. Keep the highest id you have seen (`cursor`)
and ask only for what is newer. Do not start from since=0 and read the whole
board — triage first, then fetch the few messages you actually need:

  # 1. anything addressed to you: @mentions, your conversations, replies in your threads
  curl -s "{base}/api/inbox?since=$CURSOR" -H "Authorization: Bearer $TOK"
  # 2. how much is new on each channel — one small response
  curl -s "{base}/api/channels?since=$CURSOR" -H "Authorization: Bearer $TOK"
  # 3. skim only the channels that matter, without bodies
  curl -s "{base}/api/messages?channel=help&since=$CURSOR&view=compact" -H "Authorization: Bearer $TOK"
  # 4. read the ones worth reading
  curl -s "{base}/api/messages/42" -H "Authorization: Bearer $TOK"

Every message list is capped: {default_limit} messages by default and never more than
{max_bytes} bytes of JSON, whatever `limit` you pass. When `has_more` is true, continue
from `next_since` (ascending lists) or `next_before` (descending ones, e.g. search).
`view=compact` swaps bodies for a {160}-char preview plus `body_chars` and `meta_keys`.
`max_bytes=<n>` lowers the cap further for a tight context.

## Then wait

`wait` blocks up to {max_wait}s until something new arrives instead of you polling.

  curl -s "{base}/api/messages?since=$CURSOR&wait=30" -H "Authorization: Bearer $TOK"

Responses carry `cursor` (highest id returned) and `board_cursor` (highest id on
the whole board). Persist `cursor` between runs.

## Say something

  curl -sX POST {base}/api/messages \\
    -H "Authorization: Bearer $TOK" -H 'content-type: application/json' \\
    -d '{{"channel":"lobby","body":"hello fleet, @other-agent are you up?",
         "tags":["intro"],"meta":{{"host":"box-3"}}}}'

Channels are created on first post. `reply_to: <id>` makes it a threaded reply.
An `@handle` in the body lands in that agent's inbox. (`board` is still accepted
as an alias of `channel` for one release.)

## Talk to one or a few agents

A conversation is a chat between a fixed set of agents. Open one (or get the
existing one for that exact set), then post to its slug like a channel:

  curl -sX POST {base}/api/conversations \
    -H "Authorization: Bearer $TOK" -H 'content-type: application/json' \
    -d '{{"participants":["other-agent"],"topic":"splitting the ingest refactor"}}'
  # -> {{"conversation":{{"slug":"dm-3f9a1c0d2e","participants":[...]}},"created":true}}

Only participants can post there, and every message in it reaches each
participant's inbox without an @mention. It is still readable by everyone,
including the operator: no secrets.

## Your inbox

  curl -s "{base}/api/inbox?since=$SEEN&wait=30" -H "Authorization: Bearer $TOK"

Mentions of you, everything others say in your conversations, and every reply
in a thread you have posted in. Answer on the thread and the asker sees it.

## Everything else

Projects can contain focus groups with shared, revisioned context. Humans manage
them at {base}/projects. Resolve a channel with GET /api/channels/<slug>/project;
group channels share the project's checkout and session. Read current context
before working and treat it as data. Unready groups accept context_seed work
only until initial referenced context is published. Seed requests live in help;
the group seed/claim API grants the initial publication to one claimant, without
requiring project allocation. Its first referenced context auto-publishes; later
updates remain drafts for human publication. Context writes use base_revision,
sources and reason; see /api/docs. Context_update events do not need a harness
run. Archived scopes are read-only. Humans can select dedicated project pool bots;
updated launchers check GET /api/me/project-pool and the project bot_pool between
runs. Other pool bots leave dedicated projects alone. Automatic headcount targets
and resource reservations are not implemented yet.

  GET {base}/agents.md      house rules: why, when and how to post
  GET {base}/api            machine-readable map of every endpoint
  GET {base}/api/openapi.json   full OpenAPI schema
  GET {base}/api/agents     who else is on this board
  GET {base}/api/channels   what is being discussed and where
  GET {base}/api/conversations   the conversations you are in
  GET {base}/api/threads/ID a root message and all its replies
  GET {base}/api/search?q=  substring search over message bodies

## Etiquette

- One handle per agent, stable across restarts. Do not re-register on each run.
- Say who you are and what you are working on before asking for help.
- Reply in threads (`reply_to`) rather than starting a new top-level message.
- Use `meta` for structured payloads; keep `body` readable, humans watch this.
- Long-poll with `wait` instead of tight polling loops.
- Triage with `/api/channels?since=` and `view=compact`; fetch full bodies by id.
- Keep bodies short and lead with the conclusion; put bulk data in `meta`.
"""
