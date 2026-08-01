"""Attila-AI BATTLE cockpit -- feed viewer + the /harness capability tester.

Battle-only. Serves http://localhost:8199/ : a read-only canvas map of every
unit + buildings with a hover panel (live reads + static unit-DB stats), and
http://localhost:8199/harness : THE command surface -- the interactive
capability test harness (game-style gestures: left-click AI unit = target ·
right-click = move / attack enemy under cursor · alt+right = attack ground ·
right-drag = draw the front line with a live placement ghost, Attila drag
convention · left-drag = pan · wheel = zoom · R = run · H = halt),
per-capability panels, verdict/notes persistence.
(2026-07-29: the old cockpit command bar + REPLAY browser were removed.)

Reads the mod's exporter (src/battle/publish.lua):
  data/aai_battle.json           live per-tick state (all units, camera)
  data/aai_unit_stats.json       static per-unit combat stats (built offline)
  data/aai_ability_stats.json    ability params/effects + kv rules (offline)
Harness orders go to data/aai_test_order.txt, acked in data/aai_test_ack.json
(src/battle/harness.lua; gate flag data/aai_harness_on.txt).

A second page, /harness, is the capability TEST HARNESS: it renders
reference/CAPABILITIES_PLAN.md as an interactive checklist (pass/fail verdicts
persisted to reference/capability_verdicts.json) and fires one-shot test
orders to src/battle/harness.lua via the data/aai_test_order.txt mailbox,
acked in data/aai_test_ack.json (conflict phase only; gate flag
data/aai_harness_on.txt).

Spawned at boot by src/frontend/spawn_viz.lua; duplicate instances exit because
the port is bound. Run by hand: python viz/aai_viz.py
"""

import glob
import json
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 8199


def find_game_dir():
    cwd = os.getcwd()
    if os.path.isfile(os.path.join(cwd, "data", "attila_ai_log.txt")):
        return cwd
    return r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila"


GAME = find_game_dir()
DATA = os.path.join(GAME, "data")
BATTLE_FILE = os.path.join(DATA, "aai_battle.json")
GEOMETRY_FILE = os.path.join(DATA, "aai_battle_geometry.json")
STATS_FILE = os.path.join(DATA, "aai_unit_stats.json")
ORDERS_OUT = os.path.join(DATA, "aai_orders.txt")
LOG_PATH = os.path.join(DATA, "aai_viz_log.txt")

# The game's Lua stores numbers as 32-bit floats: an epoch-ms seq collapses
# (~131 s step near 1.78e12), silently dropping orders. Emit a SMALL monotonic
# counter instead -- exact in float32 (< 2^24), never goes backwards.
_SEQ_EPOCH = 1783000000
_seq_counter = 0
# order slots: (unit_key, channel) -> (line, expiry). channel "move" is the
# single movement/attack slot; stances are independent; a stance never clobbers
# a standing move. Re-listed every write; ai_link ignores identical replays.
_pending = {}
ORDER_TTL = 120.0

_cache = {}  # path -> (data, mtime)


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except OSError:
        pass


def next_seq():
    global _seq_counter
    _seq_counter = max(_seq_counter + 1, int(time.time()) - _SEQ_EPOCH)
    return _seq_counter


def read_json(path):
    """Return (data, age_seconds) with an mtime cache; tolerate the atomic-write gap."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None, None
    cached = _cache.get(path)
    if cached and cached[1] == mt:
        return cached[0], round(time.time() - mt, 1)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # mid-write; keep last good
        if cached:
            return cached[0], round(time.time() - mt, 1)
        return None, None
    _cache[path] = (data, mt)
    return data, round(time.time() - mt, 1)


# (2026-07-29: the cockpit AI-control order queue and the replay browser were
# removed -- the /harness page is the single command surface now.)


# ----------------------------------------------------- capability harness
#
# /harness backend: parse reference/CAPABILITIES_PLAN.md into a
# section/subsection/item manifest (T1/T2 only -- native tiers are out of
# harness scope), persist pass/fail verdicts, and relay ONE test order at a
# time to src/battle/harness.lua via the seq-gated two-line mailbox
# data/aai_test_order.txt, acked in data/aai_test_ack.json.

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_FILE = os.path.join(REPO, "reference", "CAPABILITIES_PLAN.md")
VERDICTS_FILE = os.path.join(REPO, "reference", "capability_verdicts.json")
NOTES_FILE = os.path.join(REPO, "reference", "harness_notes.md")
TEST_ORDER_OUT = os.path.join(DATA, "aai_test_order.txt")
TEST_ACK_FILE = os.path.join(DATA, "aai_test_ack.json")
CMDS_FILE = os.path.join(DATA, "aai_cmds.json")
ABILSTATS_FILE = os.path.join(DATA, "aai_ability_stats.json")
BLD_FILE = os.path.join(DATA, "aai_bld.json")
CAPEV_FILE = os.path.join(DATA, "aai_capev.json")


def load_notes():
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def save_notes(text):
    tmp = NOTES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text if isinstance(text, str) else "")
    os.replace(tmp, NOTES_FILE)

# the exact verb grammar of src/battle/harness.lua's VERBS table
# (2026-07-29 cull: teleport/speed/attack-line/morale/fatigue/attrition/cheat
# and cosmetic verbs removed at Theo's order — unit-level control only)
HARNESS_VERBS = frozenset((
    "take", "release", "halt", "move", "form", "apos", "occupy",
    "withdraw", "rotate", "stepf", "stepb", "faw", "mlee", "shot", "aunit",
    "beh", "winc", "wdec", "abil", "enabled",
    "battk", "climb", "leavebld", "defendbld", "usedep", "usedep2", "deployr",
    "freeze"))

# harness.lua's GLOBALS table: battlefield probes, no unit key (some no-arg)
# (battle-level write levers all removed 2026-07-29 — "Dont need")
HARNESS_GLOBALS = frozenset((
    "freeze", "elev", "bld", "bldstep", "bdestroyi", "aeq", "vp", "ships"))

_TIER_RE = re.compile(r"^T(\d)\s*(.*)$", re.S)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse_plan():
    """CAPABILITIES_PLAN.md -> [{title, subsections: [{title, desc, items}]}].
    Items are '- name <em-dash> YES|not yet <em-dash> T# method' lines; native
    tiers (T3+) are skipped; any other line inside a subsection joins its
    description. A section with no '###' gets one implicit '' subsection."""
    try:
        with open(PLAN_FILE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    sections, sec, sub = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if raw.startswith("## ") and not raw.startswith("###"):
            sec = {"title": raw[3:].strip(), "subsections": []}
            sections.append(sec)
            sub = None
            continue
        if raw.startswith("### "):
            if sec is not None:
                sub = {"title": raw[4:].strip(), "desc": "", "items": []}
                sec["subsections"].append(sub)
            continue
        if sec is None or not line or line == "---":
            continue
        if sub is None:                          # section without ### headers
            sub = {"title": "", "desc": "", "items": []}
            sec["subsections"].append(sub)
        item = None
        if line.startswith("- "):
            parts = line[2:].split(" %s " % chr(8212), 2)  # em-dash sep
            if len(parts) == 3 and parts[1] in ("YES", "not yet"):
                m = _TIER_RE.match(parts[2].strip())
                if m:
                    item = (parts[0].strip(), parts[1], int(m.group(1)),
                            m.group(2).strip())
        if item is None:
            sub["desc"] = (sub["desc"] + " " + line).strip()
            continue
        name, status, tier, method = item
        if tier >= 3:                            # native tiers: not harness-testable
            continue
        sub["items"].append({
            "id": slugify(sec["title"] + "|" + sub["title"] + "|" + name),
            "name": name, "status": status, "tier": tier, "method": method})
    return sections


def load_verdicts():
    try:
        with open(VERDICTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_verdict(req):
    vid = req.get("id")
    verdict = req.get("verdict")
    if not vid or verdict not in ("pass", "fail", None):
        raise ValueError("need an id and verdict pass|fail|null")
    note = str(req.get("note") or "")
    v = load_verdicts()
    if verdict is None and not note:
        # only a fully empty row deletes; a note with no verdict PERSISTS
        # (losing typed notes on verdict-toggle was a real data-loss bug)
        v.pop(vid, None)
    else:
        v[vid] = {"verdict": verdict, "note": note, "ts": int(time.time())}
    tmp = VERDICTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=1, sort_keys=True)
    os.replace(tmp, VERDICTS_FILE)


def capabilities_payload():
    sections = parse_plan()
    verdicts = load_verdicts()
    for sec in sections:
        for sub in sec["subsections"]:
            for it in sub["items"]:
                v = verdicts.get(it["id"]) or {}
                it["verdict"] = v.get("verdict")
                it["note"] = v.get("note", "")
    return {"sections": sections}


_test_seq = None     # monotonic; resumed from the mailbox file across restarts

# ORDER QUEUE (2026-07-29, Theo's ask): the mailbox is one-slot and drains on
# battle ticks, so orders issued while the game is PAUSED used to overwrite
# each other -- only the last fired on unpause. Now orders queue here and the
# next one is only written to the mailbox once the previous seq acks, exactly
# like queueing orders in the real game. A jammed order (no ack while the
# battle feed is visibly TICKING -> harness off / not in battle) is dropped
# after 20 s; a stale feed (paused / loading) holds the queue indefinitely.
_order_q = []        # [(seq, line)] not yet written to the mailbox
_inflight = None     # seq currently in the mailbox, unacked
_inflight_t = 0.0
_q_lock = threading.Lock()


# ---- cast log: cockpit-side accumulation of cast events ------------------
# The game's rows ring (aai_cmds.json) holds only the last 30 command events
# and Halt spam can flush a cast out within seconds; the cockpit therefore
# accumulates every "Special Ability" cast name itself (n-delta over the ring
# tail), giving the page a persistent whole-battle read that survives ring
# flooding and page refreshes. Battle-wide only: the events carry no unit
# attribution (see the active-formation plan line).
_cast_acc = {}       # name -> {"c": count, "t": last-seen time.time()}
_cast_seen_n = 0
_cast_lock = threading.Lock()


def _cast_pump():
    global _cast_seen_n
    while True:
        try:
            cm, _age = read_json(CMDS_FILE)
            if isinstance(cm, dict) and isinstance(cm.get("n"), int):
                n = cm["n"]
                with _cast_lock:
                    if n < _cast_seen_n:      # counter restarted = new battle
                        _cast_acc.clear()
                        _cast_seen_n = 0
                    k = n - _cast_seen_n
                    if k > 0:
                        rows = cm.get("rows") or []
                        for c in rows[-min(k, len(rows)):]:
                            if "Special Ability" not in str(c.get("nm", "")):
                                continue
                            m = re.search(r"get_string1=(\S+)",
                                          str(c.get("extra", "")))
                            if m:
                                e = _cast_acc.setdefault(
                                    m.group(1), {"c": 0, "t": 0.0})
                                e["c"] += 1
                                e["t"] = time.time()
                        _cast_seen_n = n
        except Exception:
            pass
        time.sleep(1.0)


threading.Thread(target=_cast_pump, daemon=True).start()


def _dispatch_locked():
    """Advance the queue (call with _q_lock held)."""
    global _inflight, _inflight_t
    if _inflight is not None:
        ack, _age = read_json(TEST_ACK_FILE)
        seq = ack.get("seq") if isinstance(ack, dict) else None
        if isinstance(seq, (int, float)) and seq >= _inflight:
            _inflight = None
        else:
            _battle, age = read_json(BATTLE_FILE)
            if time.time() - _inflight_t > 20 and age is not None and age < 3:
                log("order queue: #%d never acked with a live feed - dropped"
                    % _inflight)
                _inflight = None
    if _inflight is None and _order_q:
        seq, line = _order_q.pop(0)
        tmp = TEST_ORDER_OUT + ".tmp"
        with open(tmp, "w") as f:
            f.write("seq %d\n%s\n" % (seq, line))
        os.replace(tmp, TEST_ORDER_OUT)
        _inflight = seq
        _inflight_t = time.time()
        log("test order #%d: %s%s" % (seq, line,
            " (+%d queued)" % len(_order_q) if _order_q else ""))


def pump_orders():
    """Advance the order queue; piggybacks on the client's own polling."""
    if _inflight is None and not _order_q:
        return
    with _q_lock:
        _dispatch_locked()


def send_test_order(req):
    """Validate + enqueue one harness order; dispatch as soon as its turn comes."""
    global _test_seq
    line = " ".join(str(req.get("line") or "").split())
    parts = line.split(" ")
    ok = (parts[0] in HARNESS_GLOBALS and len(parts) >= 1) or \
         (parts[0] in HARNESS_VERBS and len(parts) >= 2)
    if not ok:
        raise ValueError("bad harness line (want '<verb> <key> [args]'): %r" % line)
    with _q_lock:
        if _test_seq is None:
            _test_seq = 0
            try:
                with open(TEST_ORDER_OUT, encoding="utf-8") as f:
                    m = re.match(r"^seq\s+(\d+)", f.readline())
                if m:
                    _test_seq = int(m.group(1))
            except OSError:
                pass
        _test_seq += 1
        _order_q.append((_test_seq, line))
        _dispatch_locked()
        return _test_seq, len(_order_q)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AAI Battle Cockpit</title>
<style>
  :root{--bg:#0c0f14;--panel:#141a23;--line:#2a3340;--txt:#d6dde6;--dim:#8797a8;
        --gold:#d9b45b;--ai:#39d3c8;--enemy:#e8863b;--atk:#ff3b3b;--mv:#7dd3fc;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
    font:13px/1.4 ui-monospace,Menlo,Consolas,monospace;overflow:hidden}
  #top{display:flex;align-items:center;gap:16px;padding:7px 12px;
    border-bottom:1px solid var(--line);background:var(--panel)}
  #top h1{font-size:13px;margin:0;color:var(--gold);letter-spacing:1px;font-weight:700}
  .seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .seg button{background:transparent;color:var(--dim);border:0;padding:4px 12px;
    cursor:pointer;font:inherit}
  .seg button.on{background:var(--gold);color:#1a1206;font-weight:700}
  .meta{color:var(--dim)} .meta b{color:var(--txt)}
  #rep{display:none;align-items:center;gap:8px;flex:1}
  #rep.show{display:flex}
  #rep select{background:#0c0f14;color:var(--txt);border:1px solid var(--line);
    border-radius:5px;padding:3px 6px;font:inherit;max-width:280px}
  #tl{flex:1} input[type=range]{width:100%}
  #main{position:relative;height:calc(100% - 46px - 108px)}
  #c{display:block;width:100%;height:100%;background:
     radial-gradient(circle at 50% 40%,#121821,#0a0d12);cursor:crosshair}
  #side{position:absolute;top:10px;right:10px;width:240px;background:rgba(16,22,30,.94);
    border:1px solid var(--line);border-radius:8px;padding:10px 12px;pointer-events:none;
    display:none}
  #side.show{display:block}
  #side h3{margin:0 0 4px;font-size:13px;color:var(--gold)}
  #side .cls{color:var(--dim);margin-bottom:6px}
  #side .row{display:flex;justify-content:space-between;gap:10px}
  #side .row span:first-child{color:var(--dim)}
  #side .shdr{margin:8px 0 3px;color:var(--gold);border-top:1px solid var(--line);
    padding-top:6px;font-size:11px;letter-spacing:.5px}
  #bar{height:108px;border-top:1px solid var(--line);background:var(--panel);
    padding:8px 12px;display:flex;flex-direction:column;gap:6px}
  #barhead{color:var(--dim);font-size:12px}
  #barhead b{color:var(--ai)}
  #btns{display:flex;flex-wrap:wrap;gap:6px}
  .tw{min-width:74px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;
    background:linear-gradient(#1c2532,#141a23);color:var(--txt);cursor:pointer;
    font:inherit;text-align:center}
  .tw:hover{border-color:var(--gold)}
  .tw.on{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold) inset;color:var(--gold)}
  .tw.mode.on{background:linear-gradient(#3a2f12,#2a2410)}
  .tw.cheat{border-color:#4a3340;color:#c39aa8}
  .tw.cheat:hover{border-color:var(--atk);color:var(--atk)}
  .tw.danger:hover{border-color:var(--atk);color:var(--atk)}
  .tw:disabled{opacity:.28;cursor:default}
  .hint{color:var(--dim);font-size:11px}
</style></head><body>
<div id="top">
  <h1>AAI BATTLE</h1>
  <a href="/harness" style="color:var(--dim);border:1px solid var(--line);border-radius:6px;
    padding:3px 10px;text-decoration:none">harness</a>
  <span class="meta" id="status"></span>
</div>
<div id="main" style="height:calc(100% - 46px)">
  <canvas id="c"></canvas>
  <div id="side"></div>
</div>
<script>
// VIEWER ONLY (2026-07-29): AI control + replay removed -- the /harness page is
// the single place orders are issued from now.
var cv=document.getElementById("c"), ctx=cv.getContext("2d");
var geom=null, state=null, stats={};
var view={zoom:1,panx:0,pany:0}, fitC=null;
var hover=null;
var drag=null;                     // pan gesture
var battleAge=0;

function resize(){cv.width=cv.clientWidth; cv.height=cv.clientHeight;}
window.addEventListener("resize",resize); resize();

// ---- data source ----
function curState(){ return state; }
function curGeom(){ return geom; }

function allUnits(st){
  var out=[]; if(!st||!st.alliances) return out;
  var pa=st.player_alliance||1;
  st.alliances.forEach(function(al,ai){
    var side=((ai+1)===pa)?"player":"ai";
    (al.armies||[]).forEach(function(ar){
      (ar.units||[]).forEach(function(u){ out.push({u:u,side:side,key:u.key||null}); });
    });
  });
  return out;
}
function findUnit(st,key){
  if(key==null) return null;
  var f=allUnits(st).filter(function(o){return o.key===key;});
  return f.length?f[0]:null;
}

// ---- projection ----
function bounds(){
  var g=curGeom();
  if(g&&g.bounds&&g.bounds.minx!=null){var b=g.bounds;
    return {minx:b.minx,minz:b.minz,maxx:b.maxx,maxz:b.maxz};}
  var st=curState(), us=allUnits(st), b={minx:1e9,minz:1e9,maxx:-1e9,maxz:-1e9};
  us.forEach(function(o){var p=o.u.pos; if(!p)return;
    b.minx=Math.min(b.minx,p.x); b.maxx=Math.max(b.maxx,p.x);
    b.minz=Math.min(b.minz,p.z); b.maxz=Math.max(b.maxz,p.z);});
  if(b.minx>b.maxx) return {minx:-500,minz:-500,maxx:500,maxz:500};
  return b;
}
function computeFit(){
  var b=bounds(), pad=50;
  var bw=Math.max(1,b.maxx-b.minx), bh=Math.max(1,b.maxz-b.minz);
  var s=Math.min((cv.width-2*pad)/bw,(cv.height-2*pad)/bh);
  fitC={s:s,cx:(b.minx+b.maxx)/2,cz:(b.minz+b.maxz)/2};
}
function w2s(x,z){var S=fitC.s*view.zoom;
  return [cv.width/2+(x-fitC.cx)*S+view.panx, cv.height/2-(z-fitC.cz)*S+view.pany];}
function s2w(sx,sy){var S=fitC.s*view.zoom;
  return [(sx-cv.width/2-view.panx)/S+fitC.cx, -(sy-cv.height/2-view.pany)/S+fitC.cz];}

// ---- render ----
function drawBuildings(){
  var g=curGeom(); if(!g||!g.buildings) return;
  var S=fitC.s*view.zoom, sz=Math.max(1.5,Math.min(6,S*3));
  ctx.globalAlpha=.9;
  g.buildings.forEach(function(b){
    var p=w2s(b.x,b.z);
    if(p[0]<-20||p[0]>cv.width+20||p[1]<-20||p[1]>cv.height+20) return;
    var wall=/wall|gate|tower|door|rampart|palis|fence|barricade/i.test(b.n||"");
    ctx.fillStyle= wall?"#7d8794":(b.o===1?"#3a4a5a":b.o===2?"#5a4030":"#39424f");
    ctx.fillRect(p[0]-sz/2,p[1]-sz/2,sz,sz);
  });
  ctx.globalAlpha=1;
}
function unitFoot(u){ // world-units half-frontage / half-depth
  if(u.width>0){ return {hw:Math.max(6,u.width/2), hd:Math.max(4,u.width/6)}; } // live frontage if any
  // else estimate: box AREA scales with men; frontage:depth ratio & density by class
  var men=u.men||u.men0||40;
  var apm=u.cavalry?2.4:(u.arty?3.0:1.2);      // world area per man
  var ratio=u.cavalry?2.0:(u.arty?1.5:4.0);    // frontage : depth
  var area=Math.max(20,men*apm);
  var front=Math.sqrt(area*ratio), depth=Math.sqrt(area/ratio);
  return {hw:Math.max(6,front/2), hd:Math.max(4,depth/2)};
}
function drawUnit(o){
  var u=o.u, p=u.pos; if(!p) return;
  var c=w2s(p.x,p.z), S=fitC.s*view.zoom;
  var f=unitFoot(u), br=(u.bearing||0)*Math.PI/180;
  var col=(o.side==="ai")?"#39d3c8":"#e8863b";
  if(u.routing||u.shattered) col=(o.side==="ai")?"#1f6f6a":"#8a5a33";
  ctx.save(); ctx.translate(c[0],c[1]);
  ctx.rotate(br);            // canvas rotate is clockwise on screen = compass sense
  var hw=f.hw*S, hd=f.hd*S;
  ctx.fillStyle=col; ctx.globalAlpha=(u.vis===false&&o.side==="ai")?.4:.92;
  ctx.fillRect(-hw,-hd,2*hw,2*hd);
  ctx.globalAlpha=1;
  ctx.strokeStyle="rgba(255,255,255,.5)"; ctx.beginPath();
  ctx.moveTo(0,0); ctx.lineTo(0,-hd-4); ctx.stroke();
  ctx.restore();
  var bw=Math.max(14,hw*2), bx=c[0]-bw/2, by=c[1]-hd-9;
  var hp=(u.men0&&u.men!=null)?u.men/u.men0:1;
  ctx.fillStyle="#20262e"; ctx.fillRect(bx,by,bw,3);
  ctx.fillStyle= hp>.5?"#4ade80":hp>.25?"#facc15":"#ef4444";
  ctx.fillRect(bx,by,bw*Math.max(0,Math.min(1,hp)),3);
  if(u.ammo0){ var am=u.ammo/u.ammo0;
    ctx.fillStyle="#20262e"; ctx.fillRect(bx,by-4,bw,2);
    ctx.fillStyle="#60a5fa"; ctx.fillRect(bx,by-4,bw*Math.max(0,Math.min(1,am)),2); }
}
function drawPath(o){
  var u=o.u; if(o.side!=="ai"||!u.ordered||!u.pos) return;
  var d=Math.hypot(u.ordered.x-u.pos.x,u.ordered.z-u.pos.z);
  if(d<3) return;
  var a=w2s(u.pos.x,u.pos.z), b=w2s(u.ordered.x,u.ordered.z);
  ctx.strokeStyle="#7dd3fc"; ctx.globalAlpha=.85; ctx.lineWidth=1.5;
  ctx.setLineDash([5,4]); ctx.beginPath();
  ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
  ctx.setLineDash([]);
  var ang=Math.atan2(b[1]-a[1],b[0]-a[0]);
  ctx.beginPath(); ctx.moveTo(b[0],b[1]);
  ctx.lineTo(b[0]-9*Math.cos(ang-0.4),b[1]-9*Math.sin(ang-0.4));
  ctx.lineTo(b[0]-9*Math.cos(ang+0.4),b[1]-9*Math.sin(ang+0.4));
  ctx.closePath(); ctx.fillStyle=ctx.strokeStyle; ctx.fill();
  ctx.globalAlpha=1; ctx.lineWidth=1;
}
function drawRing(o){ // range ring, hover only
  var u=o.u; if(!u.range||u.range<=0||!u.pos) return;
  var c=w2s(u.pos.x,u.pos.z), r=u.range*fitC.s*view.zoom;
  ctx.beginPath(); ctx.arc(c[0],c[1],r,0,7);
  ctx.fillStyle="rgba(217,180,91,.08)"; ctx.fill();
  ctx.strokeStyle="rgba(217,180,91,.6)"; ctx.setLineDash([4,4]);
  ctx.stroke(); ctx.setLineDash([]);
}
function render(){
  var st=curState();
  computeFit();
  ctx.clearRect(0,0,cv.width,cv.height);
  drawBuildings();
  var us=allUnits(st);
  us.forEach(drawPath);
  us.forEach(drawUnit);
  if(hover){ var ho=(hover.key!=null?findUnit(st,hover.key):null)||hover; drawRing(ho); }
  updateStatus(st);
  requestAnimationFrame(render);
}

// ---- hover panel ----
var SLAB={melee_attack:"melee atk",melee_defence:"melee def",weapon_damage:"wpn dmg",
  weapon_ap_damage:"wpn ap dmg",charge_bonus:"charge",armour:"armour",
  shield_defence:"shield",missile_block_chance:"msl block",hitpoints:"hp",
  bonus_hit_points:"bonus hp",morale:"morale",speed:"speed",charge_speed:"charge spd",
  mass:"mass",weapon_bonus_v_cavalry:"vs cav",weapon_bonus_v_infantry:"vs inf",
  missile_range:"msl range",missile_damage:"msl dmg",missile_ap_damage:"msl ap dmg",
  ammo:"ammo (base)",reload:"reload",training_level:"training",caste:"caste",tier:"tier"};
var SORDER=["melee_attack","melee_defence","weapon_damage","weapon_ap_damage","charge_bonus",
  "armour","shield_defence","missile_block_chance","hitpoints","bonus_hit_points","morale",
  "speed","charge_speed","mass","weapon_bonus_v_cavalry","weapon_bonus_v_infantry",
  "missile_range","missile_damage","missile_ap_damage","ammo","reload",
  "training_level","caste","tier"];
function statBlock(u){
  var s=stats[u.type];
  if(!s){ return '<div class="shdr">unit stats</div><div class="hint">no DB stats yet</div>'; }
  var seen={}, rows="";
  SORDER.forEach(function(k){ if(s[k]!=null&&!seen[SLAB[k]||k]){ seen[SLAB[k]||k]=1;
    rows+='<div class="row"><span>'+(SLAB[k]||k)+'</span><span>'+esc(s[k])+'</span></div>'; }});
  var abil=(s.abilities&&s.abilities.length)?
    '<div class="row"><span>abilities</span><span>'+esc(s.abilities.join(", "))+'</span></div>':"";
  return '<div class="shdr">unit stats</div>'+(rows||'<div class="hint">—</div>')+abil;
}
function showSide(o){
  var s=document.getElementById("side");
  if(!o){ s.className=""; return; }
  var u=o.u, ammo=(u.ammo0? (u.ammo+"/"+u.ammo0):"-");
  function row(k,v){return '<div class="row"><span>'+k+'</span><span>'+v+'</span></div>';}
  s.innerHTML='<h3>'+(o.side==="ai"?"◆ ":"")+esc(u.type||u.name||"?")+'</h3>'+
    '<div class="cls">'+esc(u.cls||"")+(o.side==="ai"?"  ·  key "+esc(o.key||""):"  (enemy)")+'</div>'+
    '<div class="shdr">live</div>'+
    row("men",(u.men!=null?u.men:"?")+" / "+(u.men0!=null?u.men0:"?"))+
    row("ammo",ammo)+ (u.range>0?row("range",Math.round(u.range)+"  (ring)"):"")+
    row("bearing",u.bearing!=null?Math.round(u.bearing)+"°":"?")+
    (u.faw!=null?row("fire-at-will",u.faw?"ON":"off"):"")+
    (u.fatigue!=null?row("fatigue",Math.round(u.fatigue*100)+"%  (native)"):"")+
    (u.has_ability?row("ability",u.ability?esc(u.ability):(u.can_ability?"ready":"has")):"")+
    row("status",[u.routing&&"ROUT",u.shattered&&"SHAT",u.moving&&"moving",
      u.idle&&"idle",u.under_fire&&"under fire"].filter(Boolean).join(", ")||"—")+
    statBlock(u);
  s.className="show";
}
function esc(s){return String(s==null?"":s).replace(/[&<>]/g,function(c){
  return{"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});}

// ---- interaction (viewer: pan/zoom/hover only) ----
function hitUnit(sx,sy,side){
  var st=curState(), best=null, bd=1e9;
  allUnits(st).forEach(function(o){ if(!o.u.pos) return; if(side&&o.side!==side) return;
    var c=w2s(o.u.pos.x,o.u.pos.z), d=Math.hypot(c[0]-sx,c[1]-sy);
    var f=unitFoot(o.u), r=Math.max(10,f.hw*fitC.s*view.zoom);
    if(d<r && d<bd){bd=d; best=o;} });
  return best;
}
function relPos(e){ var r=cv.getBoundingClientRect(); return [e.clientX-r.left,e.clientY-r.top]; }
cv.addEventListener("mousedown",function(e){
  var p=relPos(e);
  drag={btn:e.button,sx0:p[0],sy0:p[1],sxl:p[0],syl:p[1],moved:false};
  if(e.button===2) e.preventDefault();
});
cv.addEventListener("mousemove",function(e){
  var p=relPos(e);
  if(drag){
    if(Math.hypot(p[0]-drag.sx0,p[1]-drag.sy0)>4) drag.moved=true;
    if(drag.btn===0){ view.panx+=p[0]-drag.sxl; view.pany+=p[1]-drag.syl; }
    drag.sxl=p[0]; drag.syl=p[1];
    return;
  }
  var o=hitUnit(p[0],p[1]); hover=o; showSide(o);
});
window.addEventListener("mouseup",function(){ drag=null; });
cv.addEventListener("contextmenu",function(e){e.preventDefault();});
cv.addEventListener("wheel",function(e){
  e.preventDefault();
  var p=relPos(e), before=s2w(p[0],p[1]), f=e.deltaY<0?1.12:1/1.12;
  view.zoom=Math.max(.2,Math.min(12,view.zoom*f));
  computeFit(); var after=s2w(p[0],p[1]), S=fitC.s*view.zoom;
  view.panx+=(after[0]-before[0])*S; view.pany-=(after[1]-before[1])*S;
},{passive:false});
// ---- status ----
function updateStatus(st){
  var el=document.getElementById("status");
  if(!st){ el.innerHTML='<span style="color:#e8863b">waiting for battle…</span>'; return; }
  el.innerHTML='phase <b>'+esc(st.phase||"?")+'</b> · t <b>'+(st.t!=null?st.t.toFixed(0):"?")+
    's</b>'+(st.remaining!=null?' · rem <b>'+Math.round(st.remaining)+'s</b>':'')+
    ' · units <b>'+allUnits(st).length+'</b> · age '+battleAge+'s';
}

// ---- live polling ----
function poll(){
  fetch("/state",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
    var inc=d.battle; battleAge=(d.battle_age==null?99:d.battle_age);
    if(!inc){ state=null; }
    else if(allUnits(inc).length>0 || !state || allUnits(state).length===0 || inc.phase==="complete"){
      state=inc;                    // accept real frames, first frame, or battle-end
    }                               // else: transient empty frame -> keep last good state
    if(!geom && inc) loadGeom();   // written once per battle; retry until it lands
  }).catch(function(){});
}
function loadGeom(){
  fetch("/geometry",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(d){ if(d&&d.buildings&&d.buildings.length) geom=d; }).catch(function(){});
}
function loadStats(){
  fetch("/unitstats",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(d){ if(d&&typeof d==="object") stats=d; }).catch(function(){});
}

loadGeom(); loadStats(); setInterval(poll,500); poll(); render();
</script>
</body></html>
"""


PAGE_HARNESS = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AAI Test Harness</title>
<style>
  :root{--bg:#0c0f14;--panel:#141a23;--line:#2a3340;--txt:#d6dde6;--dim:#8797a8;
        --gold:#d9b45b;--blue:#5aa2ff;--red:#ff5b5b;--green:#4ade80;--amber:#f0a840;
        --t2:#3dd68c;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
    font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;overflow:hidden}
  #top{display:flex;align-items:center;gap:14px;padding:7px 12px;height:46px;
    border-bottom:1px solid var(--line);background:var(--panel);white-space:nowrap;
    overflow:hidden}
  #top h1{font-size:13px;margin:0;color:var(--gold);letter-spacing:1px;font-weight:700}
  .lnk{color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:3px 10px;
    text-decoration:none}
  .lnk:hover{border-color:var(--gold);color:var(--gold)}
  .meta{color:var(--dim)} .meta b{color:var(--txt)}
  .bad{color:var(--red);font-weight:700}
  #warn{display:none;color:#1a1206;background:var(--amber);border-radius:5px;
    padding:2px 8px;font-weight:700}
  #wrap{display:flex;height:calc(100% - 46px)}
  #nav{width:295px;min-width:295px;overflow-y:auto;border-right:1px solid var(--line);
    background:var(--panel);padding:6px 0 30px}
  .nsec{padding:10px 12px 3px;color:var(--gold);font-size:11px;letter-spacing:.6px;
    text-transform:uppercase}
  .nsub{display:flex;justify-content:space-between;gap:8px;padding:4px 12px 4px 18px;
    cursor:pointer}
  .nsub:hover{background:#1a2230}
  .nsub.on{background:#22304a;color:#fff}
  .ndots{color:var(--dim);white-space:nowrap;font-size:11px}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin:0 2px 0 6px}
  .dp{background:var(--green)} .df{background:var(--red)} .du{background:#4a5665}
  #panel{flex:1;overflow-y:auto;padding:12px 16px 60px}
  .crumb{color:var(--dim)} .crumb b{color:var(--gold)}
  .sdesc{color:var(--dim);margin:4px 0 0;font-size:12px}
  .wgt{display:flex;gap:12px;margin:10px 0;align-items:stretch;flex-wrap:wrap}
  .mapcol{flex:2 1 340px;min-width:300px}
  #map{display:block;width:100%;height:330px;border:1px solid var(--line);border-radius:8px;
    background:radial-gradient(circle at 50% 40%,#121821,#0a0d12);cursor:crosshair}
  .dtl,.ctrl{flex:1 1 280px;min-width:260px;background:rgba(16,22,30,.94);
    border:1px solid var(--line);border-radius:8px;padding:10px 12px;
    max-height:330px;overflow-y:auto}
  .ulist{flex:1 1 250px;min-width:230px;background:rgba(16,22,30,.94);
    border:1px solid var(--line);border-radius:8px;max-height:330px;overflow-y:auto;
    padding:4px 0}
  .ulrow{display:flex;gap:8px;padding:2px 10px;align-items:center;cursor:default}
  .ulrow:hover{background:#1a2230}
  .udot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
  .row{display:flex;justify-content:space-between;gap:10px;padding:1px 0}
  .row span:first-child{color:var(--dim)}
  .gv{color:#4a5665}
  .shdr{margin:8px 0 3px;color:var(--gold);border-top:1px solid var(--line);
    padding-top:6px;font-size:11px;letter-spacing:.5px}
  .bdg{display:inline-block;border:1px solid var(--line);color:#4a5665;border-radius:4px;
    padding:1px 6px;margin:2px 3px 0 0;font-size:11px}
  .bdg.on{border-color:var(--green);color:var(--green)}
  .brow{display:flex;align-items:center;gap:8px;padding:2px 0}
  .brow span:first-child{color:var(--dim);width:44px;flex:none}
  .brow span:last-child{white-space:nowrap}
  .bwrap{flex:1;height:7px;background:#20262e;border-radius:4px;overflow:hidden;display:block}
  .bwrap i{display:block;height:100%}
  .item{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0;
    background:var(--panel)}
  .itop{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .iname{font-weight:700}
  .chip{border-radius:4px;padding:1px 7px;font-size:11px;font-weight:700;flex:none}
  .chip.t1{background:#173154;color:var(--blue)}
  .chip.t2{background:#12362a;color:var(--t2)}
  .chip.yes{background:#143322;color:var(--green)}
  .chip.nyet{background:#332a14;color:var(--amber)}
  .chip.ok{background:#143322;color:var(--green)}
  .chip.pend{background:#33301a;color:var(--amber)}
  .chip.err{background:#3a1a1a;color:var(--red)}
  .chip.skip{background:#242a33;color:var(--dim)}
  .mth{color:var(--dim);margin:3px 0 0}
  .mth code{color:#a8c7e8}
  .ictl{display:flex;align-items:center;gap:8px;margin-top:6px}
  .vb{border:1px solid var(--line);background:linear-gradient(#1c2532,#141a23);
    color:var(--dim);border-radius:6px;padding:3px 12px;cursor:pointer;font:inherit}
  .vb.pass.on{border-color:var(--green);color:var(--green);
    box-shadow:0 0 0 1px var(--green) inset}
  .vb.fail.on{border-color:var(--red);color:var(--red);
    box-shadow:0 0 0 1px var(--red) inset}
  .note{flex:1;min-width:80px}
  input,select{background:#0c0f14;color:var(--txt);border:1px solid var(--line);
    border-radius:5px;padding:3px 8px;font:inherit}
  .ctrl button{border:1px solid var(--line);background:linear-gradient(#1c2532,#141a23);
    color:var(--txt);border-radius:6px;padding:4px 9px;margin:2px 2px;cursor:pointer;
    font:inherit}
  .ctrl button:hover{border-color:var(--gold)}
  .cg{border-top:1px solid var(--line);padding:7px 0}
  .cg:first-child{border-top:0;padding-top:0}
  .cg b{color:var(--gold);font-size:11px;letter-spacing:.5px;text-transform:uppercase;
    display:block;margin-bottom:3px}
  .conrow{display:flex;align-items:center;gap:10px;margin:8px 0;flex-wrap:wrap}
  .conrow select{max-width:430px}
  #olog{margin:6px 0}
  .oline{display:flex;align-items:center;gap:8px;padding:2px 0;flex-wrap:wrap}
  .oline code{color:#a8c7e8}
  .hint{color:var(--dim);font-size:11px}
  #tip{position:fixed;display:none;background:rgba(16,22,30,.97);
    border:1px solid var(--line);border-radius:6px;padding:6px 9px;pointer-events:none;
    z-index:50;font-size:12px;max-width:260px}
  #ucard{position:fixed;display:none;background:rgba(14,19,26,.98);
    border:1px solid var(--gold);border-radius:8px;padding:10px 12px;
    left:16px;top:64px;z-index:60;width:780px;max-width:94vw;max-height:88vh;
    overflow-y:auto;font-size:12px}
  #ucard .cbody{column-count:3;column-gap:16px}
  #ucard .cbody .row,#ucard .cbody .hint,#ucard .cbody .brow{break-inside:avoid}
  #ucard .cbody .shdr{break-after:avoid;break-inside:avoid}
  #ucard h3{margin:0 0 2px;font-size:13px;color:var(--gold)}
  #toast{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);display:none;
    background:#332a14;color:var(--amber);border:1px solid var(--amber);border-radius:6px;
    padding:6px 14px;z-index:70}
  #frz{cursor:pointer;border-radius:6px;padding:3px 10px;font:inherit;
    background:#16202c;color:var(--blue);border:1px solid #2a3a4e}
  #frz.on{background:#12261a;color:var(--green);border-color:var(--green)}
  #ntog{cursor:pointer;border-radius:6px;padding:3px 10px;font:inherit;
    background:#241c30;color:#c9a2ff;border:1px solid #4a3a6e}
  #notesbox{display:none;position:fixed;right:14px;bottom:14px;width:440px;
    height:280px;z-index:95;background:#101826;border:1px solid #2a3a4e;
    border-radius:8px;box-shadow:0 6px 24px #000a;flex-direction:column}
  #notesbox.open{display:flex}
  #nhead{padding:6px 10px;font-size:11px;letter-spacing:1px;color:#c9a2ff;
    border-bottom:1px solid #2a3a4e;display:flex;justify-content:space-between}
  #nstat{color:var(--green);font-size:11px}
  #fnotes{flex:1;background:#0d1420;color:#dfe7f2;font:12px/1.5 Consolas,monospace;
    border:0;border-radius:0 0 8px 8px;padding:10px;resize:none;outline:none}
</style></head><body>
<div id="top">
  <h1>AAI TEST HARNESS</h1>
  <a class="lnk" href="/">cockpit</a>
  <span class="meta" id="feed"></span>
  <span class="meta" id="tgtlab"></span>
  <button id="frz" class="on" onclick="toggleFreeze()"
    title="Seize + halt every AI unit each tick (unit under test exempt)">AI: FROZEN</button>
  <button id="ntog" onclick="toggleNotes()"
    title="Free-form field notes -- saved to reference/harness_notes.md for Claude to analyze">notes</button>
  <span id="warn">write tests execute only in conflict phase</span>
</div>
<div id="notesbox">
  <div id="nhead"><span>FIELD NOTES &mdash; anything odd, broken, surprising</span>
    <span id="nstat"></span></div>
  <textarea id="fnotes" spellcheck="false"
    placeholder="e.g. 'phalanx read true but soldiers never re-formed' -- Claude reads this file after the session"></textarea>
</div>
<div id="wrap">
  <div id="nav"></div>
  <div id="panel">
    <div id="seghead"></div>
    <div id="widget"></div>
    <div id="items"></div>
  </div>
</div>
<div id="tip"></div>
<div id="ucard"></div>
<div id="toast"></div>
<script>
var caps=null, state=null, feedAge=null, stats={};
var active=null;                 // {si,bi}: active section/subsection
var selUid=null, targetKey=null; // inspector selection / AI command target
var clickPts=[];                 // last two world-coord map clicks (write mode)
var orders=[];                   // fired test orders, newest first (UI shows [0] only)
var hoverU=null, mouse={x:0,y:0};
var mapKind="none", mapFit=null, ulistSig=null, tselSig=null;
var noteDraft={};                // note typed before any verdict exists

// The engine's OWN toggle-tier vocabulary (empire.retail.dll parse table at
// 0x1ad3c6c, bracketed by its "Unknown normal ability" error + the ability_*
// UI ids): first three are effect-proven, the rest are candidates to A/B on
// units that plausibly have them (defend = the hold-ground toggle, dismount =
// cavalry, unlimber = artillery, board_ship = naval...).
var BEHAVIOURS=["fire_at_will","change_formation_spacing","skirmish","defend",
  "formed_attack","dismount","unlimber","release_animals",
  "drop_siege_equipment","abandon_artillery_engines","board_ship",
  "naval_fire_at_will"];
var STANCE_SET=["skirmish","defend","formed_attack","unlimber","dismount",
  "release_animals","drop_siege_equipment","abandon_artillery_engines",
  "board_ship","naval_fire_at_will"];
// (GFORMS list removed 2026-07-29: group formations = Theo's "useless tier",
// write lever culled; Change Formation events still show in the census)
var CARD_GROUPS=[
  ["melee",[["melee_attack","melee atk"],["melee_defence","melee def"],
    ["charge_bonus","charge"]]],
  ["defence",[["armour","armour"],["shield_defence","shield def"],
    ["shield_armour","shield arm"],["missile_block_chance","msl block"]]],
  ["vitality",[["morale","morale"],["hitpoints","hp"],["bonus_hit_points","bonus hp"],
    ["num_men","unit size"]]],
  ["weapon",[["weapon_damage","damage"],["weapon_ap_damage","ap dmg"],
    ["weapon_armour_piercing","armour piercing"],["weapon_bonus_v_cavalry","vs cav"],
    ["weapon_bonus_v_infantry","vs inf"]]],
  ["missile",[["ammo","ammo"],["accuracy","accuracy"],["reload","reload"],
    ["missile_range","range"],["missile_damage","damage"],
    ["missile_ap_damage","ap dmg"],["primary_missile_weapon","weapon"]]],
  ["mobility",[["speed","run spd"],["walk_speed","walk spd"],
    ["charge_speed","charge spd"],["mass","mass"]]],
  ["class & cost",[["unit_class","class"],["category","category"],["caste","caste"],
    ["tier","tier"],["training_level","training"],["recruitment_cost","recruit"],
    ["upkeep_cost","upkeep"]]],
  // 2026-07-29 extractor expansion -- groups render only when the unit has data
  ["weapon detail",[["weapon_length","length"],["weapon_bonus_v_elephants","vs eleph"],
    ["weapon_armour_penetrating","arm penetrating"],["weapon_shield_piercing","shield piercing"],
    ["weapon_first_strike","first strike"],["weapon_building_damage","bldg dmg"]]],
  ["missile detail",[["missile_minimum_range","min range"],["missile_spread","spread"],
    ["missile_marksmanship_bonus","marksmanship"],["missile_incendiary","incendiary"],
    ["missile_fire_damage","fire dmg"],["missile_can_damage_buildings","vs bldgs"],
    ["missile_bonus_v_infantry","vs inf"],["missile_bonus_v_cavalry","vs cav"],
    ["missile_bonus_v_elephant","vs eleph"]]],
  ["armour vs missiles",[["armour_bonus_v_missiles","bonus"],["armour_weak_v_missiles","weak"],
    ["mount_armour","mount armour"]]],
  ["dismounted",[["dismounted_melee_attack","melee atk"],["dismounted_melee_defence","melee def"],
    ["dismounted_charge_bonus","charge"]]],
  ["movement detail",[["acceleration","accel"],["deceleration","decel"],
    ["turn_speed","turn spd"],["radius","radius"],
    ["charge_distance_commence_run","chg run at"],
    ["charge_distance_adopt_charge_pose","chg pose at"],
    ["charge_distance_pick_target","chg pick at"],
    ["fire_arc_close","arc close"],["fire_arc_loose","arc loose"]]],
  ["formation & spacing",[["rank_depth","rank depth"],["loose_spacing","loose capable"],
    ["spacing_horizontal","spacing x"],["spacing_vertical","spacing y"],
    ["spacing_loose_horizontal","loose x"],["spacing_loose_vertical","loose y"],
    ["spacing_horde","horde"]]],
  ["spotting & stealth",[["visibility_spotting_range_min","spot min"],
    ["visibility_spotting_range_max","spot max"],["spot_dist_tree","spot tree"],
    ["spot_dist_scrub","spot scrub"],["hiding_scalar","hiding"]]],
  ["officers & sub-entities",[["officer_count","officers"],
    ["has_standard_bearer","standard"],["has_musician","musician"],
    ["num_mounts","mounts"],["num_animals","animals"],["num_chariots","chariots"],
    ["num_guns","guns"]]],
  ["capture & recharge",[["capture_power","capture power"],
    ["ability_global_recharge","abil recharge"]]],
  ["siege engine",[["engine","engine"],["engine_type","type"],
    ["engine_can_move","can move"],["engine_missile_range","range"],
    ["engine_missile_damage","damage"],["engine_missile_ap_damage","ap dmg"]]],
  ["naval",[["is_naval","naval"],["num_ships","ships"],["naval_class","class"],
    ["can_ram","can ram"],["can_board","can board"],["ignition_threshold","ignition"],
    ["ship_hitpoints","hull hp"],["ship_mass","hull mass"],["ship_speed","hull spd"]]]];

function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function fx(v){return Math.round(v*10)/10;}
function val(id){var el=document.getElementById(id);return el?el.value.trim():"";}
function num(id,d){var v=parseFloat(val(id));return isNaN(v)?d:v;}
function chk(id){var el=document.getElementById(id);return (el&&el.checked)?1:0;}
function post(url,body,cb){
  fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)}).then(function(r){return r.json();})
    .then(function(d){if(cb)cb(d);}).catch(function(){if(cb)cb(null);});
}
var toastT=null;
function toast(msg){
  var el=document.getElementById("toast"); el.textContent=msg; el.style.display="block";
  clearTimeout(toastT); toastT=setTimeout(function(){el.style.display="none";},2400);
}

// ---- units ----
var measureUid=null;   // second unit of the distance tool (r_pos segment)
function allUnits(){
  var out=[]; if(!state||!state.alliances) return out;
  var pa=state.player_alliance||1;
  state.alliances.forEach(function(al,ai){
    var side=((ai+1)===pa)?"player":"ai";
    (al.armies||[]).forEach(function(ar,ri){
      (ar.units||[]).forEach(function(u,ui){
        out.push({u:u,side:side,key:u.key||null,
                  uid:u.key?("k:"+u.key):("x:"+ai+":"+ri+":"+ui)});
      });
    });
  });
  return out;
}
function findKey(k){
  var us=allUnits();
  for(var i=0;i<us.length;i++) if(us[i].key===k) return us[i];
  return null;
}
function selUnit(){
  var us=allUnits();
  for(var i=0;i<us.length;i++) if(us[i].uid===selUid) return us[i];
  if(targetKey) return findKey(targetKey);
  return null;
}

// ---- segment kinds ----
function segKind(secT,subT){
  var s=secT.toLowerCase(), b=(subT||"").toLowerCase();
  if(s.indexOf("write")===0){
    if(b.indexOf("movement")===0) return "w_move";
    if(b.indexOf("attacking")===0) return "w_atk";
    if(b.indexOf("stances")===0) return "w_stance";
    if(b.indexOf("control")===0) return "w_meta";
    if(b.indexOf("walls")===0) return "w_siege";
    if(b.indexOf("unit abilities")===0) return "r_abil";     // FIRE rows live
    if(b.indexOf("general abilities")===0) return "r_gabil"; // on the read pages
    return "w_none";                     // naval (ram/board = T4)
  }
  if(s.indexOf("read")===0&&s.indexOf("battlefield")>=0){
    if(b.indexOf("terrain")===0) return "r_terrain";
    if(b.indexOf("buildings")===0) return "r_bld";
    if(b.indexOf("victory")===0) return "r_vp";
    return "none";
  }
  if(s.indexOf("read")===0&&s.indexOf("units")>=0){
    if(b.indexOf("unit card")===0) return "r_card";
    if(b.indexOf("identity")===0) return "r_id";
    if(b.indexOf("position")===0) return "r_pos";
    if(b.indexOf("combat")===0) return "r_combat";
    if(b.indexOf("stances")===0) return "r_stance";
    if(b.indexOf("current orders")===0) return "r_orders";
    if(b.indexOf("unit abilities")===0) return "r_abil";
    if(b.indexOf("general abilities")===0) return "r_gabil";
    return "r_gen";                      // live scalars etc.
  }
  if(s.indexOf("battle flow")>=0) return "r_flow";
  return "none";                         // battlefield (buildings/terrain/VPs)
}

// ---- nav ----
function counts(sub){
  var p=0,f=0,u=0;
  sub.items.forEach(function(it){
    if(it.verdict==="pass")p++; else if(it.verdict==="fail")f++; else u++; });
  return {p:p,f:f,u:u};
}
function renderNav(){
  var el=document.getElementById("nav");
  if(!caps){el.innerHTML='<div class="hint" style="padding:10px">loading plan...</div>';return;}
  var h="";
  caps.sections.forEach(function(sec,si){
    h+='<div class="nsec">'+esc(sec.title)+'</div>';
    sec.subsections.forEach(function(sub,bi){
      var c=counts(sub), on=active&&active.si===si&&active.bi===bi;
      h+='<div class="nsub'+(on?" on":"")+'" data-si="'+si+'" data-bi="'+bi+'">'+
        '<span>'+esc(sub.title||"(general)")+'</span><span class="ndots">'+
        (c.p?'<i class="dot dp"></i>'+c.p:"")+
        (c.f?'<i class="dot df"></i>'+c.f:"")+
        (c.u?'<i class="dot du"></i>'+c.u:"")+
        '</span></div>';
    });
  });
  el.innerHTML=h;
  el.querySelectorAll(".nsub").forEach(function(d){
    d.onclick=function(){ setActive(+d.dataset.si,+d.dataset.bi); };
  });
}
function setActive(si,bi){
  active={si:si,bi:bi}; clickPts=[]; ulistSig=null; tselSig=null;
  renderNav(); renderSeg();
}
function curSub(){
  if(!caps||!active) return null;
  var sec=caps.sections[active.si]; if(!sec) return null;
  var sub=sec.subsections[active.bi]; if(!sub) return null;
  return {sec:sec,sub:sub};
}
function findItem(id){
  var found=null;
  if(!caps) return null;
  caps.sections.forEach(function(sec){sec.subsections.forEach(function(sub){
    sub.items.forEach(function(it){ if(it.id===id) found=it; });});});
  return found;
}

// ---- segment panel ----
function renderSeg(){
  var c=curSub(), head=document.getElementById("seghead"),
      wg=document.getElementById("widget");
  if(!c){head.innerHTML="";wg.innerHTML="";document.getElementById("items").innerHTML="";return;}
  mapKind=segKind(c.sec.title,c.sub.title);
  head.innerHTML='<div class="crumb">'+esc(c.sec.title)+' &raquo; <b>'+
    esc(c.sub.title||"(general)")+'</b></div>'+
    (c.sub.desc?'<div class="sdesc">'+esc(c.sub.desc)+'</div>':"");
  wg.innerHTML=widgetHtml(mapKind);
  wireWidget();
  renderItems();
}
function widgetHtml(kind){
  if(kind==="none")
    return '<div class="hint" style="margin:8px 0">battlefield reads (buildings / terrain '+
      '/ victory points) need their dedicated probe passes &mdash; every item below says '+
      'exactly what it is waiting on; nothing here is silently untestable</div>';
  if(kind==="w_none")
    return '<div class="hint" style="margin:8px 0">siege / naval write levers &mdash; '+
      'building handles are quarantined outside the siege pass and naval ram/board is T4; '+
      'every item below carries its exact status</div>';
  if(kind==="r_flow")
    return '<div class="wgt"><div class="mapcol"><canvas id="map"></canvas>'+
      '<div class="hint" id="mapmsg">live battle-level readout on the right</div></div>'+
      '<div class="dtl" id="dtl"></div></div>'+
      '<div class="ctrl" id="ctrl">'+controlsHtml(kind)+'</div>'+
      '<div id="olog"></div>';
  if(kind==="r_terrain"||kind==="r_bld"||kind==="r_vp")
    return '<div class="wgt"><div class="mapcol"><canvas id="map"></canvas>'+
      '<div class="hint" id="mapmsg">'+
      (kind==="r_terrain"?"click the map to set the sample point, then ELEV SAMPLE":
       kind==="r_bld"?"probe results render on the right":
       "capture events tick in on the right")+'</div></div>'+
      '<div class="dtl" id="dtl"></div></div>'+
      '<div class="ctrl" id="ctrl">'+controlsHtml(kind)+'</div>'+
      '<div id="olog"></div>';
  if(kind==="r_card")
    return '<div class="wgt"><div class="mapcol"><canvas id="map"></canvas>'+
      '<div class="hint" id="mapmsg">hover any unit (map or list) for its full DB card</div></div>'+
      '<div class="ulist" id="ulist"></div></div>';
  if(kind.charAt(0)==="r")
    return '<div class="wgt"><div class="mapcol"><canvas id="map"></canvas>'+
      '<div class="hint" id="mapmsg">click = select &middot; hover = tooltip</div></div>'+
      '<div class="dtl" id="dtl"></div></div>';
  return '<div class="conrow"><span class="hint">target</span><select id="tsel"></select>'+
    '<span class="hint" id="tinfo"></span></div>'+
    '<div class="wgt"><div class="mapcol"><canvas id="map"></canvas>'+
    '<div class="hint" id="mapmsg">click map = set coords (crosshair) &middot; '+
    'click AI unit = set target</div></div>'+
    '<div class="ctrl" id="ctrl">'+controlsHtml(kind)+'</div></div>'+
    '<div id="olog"></div>';
}
function opt(list){return list.map(function(x){
  return '<option value="'+esc(x)+'">'+esc(x)+'</option>';}).join("");}
function controlsHtml(kind){
  if(kind==="w_move")
    return '<div class="cg"><b>gestures (game-style, work on every panel)</b> '+
      '<span class="hint">right-click = move · right-drag = draw the line, '+
      'the gold ghost is where the unit will stand</span> '+
      '<label><input type="checkbox" id="runchk"> run (R)</label></div>'+
      '<div class="cg"><b>motion</b>'+
      '<button data-a="halt">HALT (H)</button><button data-a="withdraw">WITHDRAW</button>'+
      '<button data-a="occupy">OCCUPY (last pt)</button>'+
      '<button data-a="rotm">ROT -45</button><button data-a="rotp">ROT +45</button>'+
      '<button data-a="stepf">STEP F</button><button data-a="stepb">STEP B</button></div>';
  if(kind==="w_atk")
    return '<div class="cg"><b>gestures</b> <span class="hint">right-click an enemy '+
      'unit = attack it · alt+right-click = attack ground</span></div>'+
      '<div class="cg"><b>ranged</b>'+
      '<button data-a="faw1">FAW ON</button><button data-a="faw0">FAW OFF</button> '+
      'shot <input id="shot" size="12" placeholder="shot type"><button data-a="shot">SET</button></div>'+
      '<div class="cg"><b>melee</b>'+
      '<button data-a="mlee1">MELEE ON</button><button data-a="mlee0">MELEE OFF</button></div>';
  if(kind==="r_flow")
    return '<div class="cg"><b>naval probe</b><button data-a="ships">SHIPS PROBE</button> '+
      '<span class="hint">walks every army, acks per-army ship + reinf-ship counts '+
      '(a:m=s#/r#) - fire this when an army visibly has ships but the rows above '+
      'show none</span></div>';
  if(kind==="r_terrain")
    return '<div class="cg"><b>elevation point sample</b>'+
      '<button data-a="elev">ELEV SAMPLE (last click)</button> '+
      '<span class="hint">the ground height at your clicked point comes back on the '+
      'ack line as OK = &lt;height&gt;; cross-check vs the terrain you see. Unit pos y '+
      '(position panel) is the same read under each unit</span></div>';
  if(kind==="r_bld")
    return '<div class="cg"><b>buildings</b>'+
      '<button data-a="bldprobe">SCAN NEXT</button> '+
      'chunk <input id="bchunk" value="250" size="5"> '+
      'mode <select id="bmode"><option value="names" selected>names only '+
      '(cold-safe test)</option><option value="lite">lite (name+pos)</option>'+
      '<option value="full">full (all fields)</option></select> '+
      '<span class="hint"><span style="color:var(--amber)">VERDICT 07-31: no '+
      'safe Lua scan exists.</span> The names-only walk (zero object calls) '+
      'still killed the feed at the first cold entry, and a cold entry&#39;s '+
      'metatable measured IDENTICAL to warm (10 keys) - cold slots are '+
      'undetectable and any touch is lethal, while warmth comes and goes with '+
      'engine streaming. Buildings move to the native/static route (offline '+
      'map geometry + native map-id). These scan buttons remain for deliberate '+
      'experiments in throwaway battles only - every click gambles the '+
      'feed</span></div>'+
      '<div class="cg"><b>freeze bisect</b>'+
      '<button data-a="bldbisect">BISECT NEXT STEP</button> '+
      'jump to step <input id="bstep" size="3"> '+
      '<span class="hint">the investigation ladder: each click makes exactly ONE '+
      'engine call from the buildings scan, in scan order (12 steps: get list, '+
      'count, item 1, its name/position, item 2, its name, the bm-level list '+
      'accessor, bulk walks; two steps are engine-free metatable checks). '+
      'Protocol: click, wait ~4 s, confirm the feed is still moving, click '+
      'again. When the feed dies, the LAST step that acked is the killer - the '+
      'ladder cannot ack past it. Leave the step box empty to auto-advance. '+
      'Warm control run passed 07-31; the decisive run is at conflict START '+
      '(cold registry). '+
      '<span style="color:var(--amber)">Throwaway battles only</span></span></div>'+
      '<div class="cg"><b>assault equipment</b><button data-a="aeq">AEQ PROBE</button> '+
      '<span class="hint">count acks; items cache for the siege console verbs '+
      '(0 outside sieges)</span></div>';
  if(kind==="r_vp")
    return '<div class="cg"><b>victory points</b>'+
      '<button data-a="vp">VP GETTER PROBE</button> '+
      '<span class="hint">re-probes fort_plazas / capture_locations / capture_points '+
      'getters live and acks what each returned; the capture-events readout above '+
      'updates on capture start/finish in a fort/siege</span></div>';
  if(kind==="w_siege")
    return '<div class="cg"><b>by building index</b> '+
      '<span class="hint">run BLD PROBE (READ &gt; Buildings) first to fill the cache; '+
      'idx = its row index</span><br>idx <input id="sbi" value="1" size="4"> '+
      '<button data-a="battk">ATTACK BLDG</button>'+
      '<button data-a="climb">CLIMB</button>'+
      '<button data-a="defendbld">DEFEND</button>'+
      '<button data-a="leavebld">LEAVE BLDG</button></div>'+
      '<div class="cg"><b>deployables (by aeq index)</b> idx <input id="sdi" value="1" '+
      'size="4"> <button data-a="usedep">OCCUPY VEHICLE</button>'+
      '<button data-a="usedep2">INTERACT</button> '+
      '<span class="hint">run AEQ PROBE first (READ &gt; Buildings)</span></div>';
  if(kind==="w_stance")
    return '<div class="cg"><span class="hint">the layers, disambiguated: '+
      '<b>modes</b> = behaviour toggles (fire-at-will, loose, skirmish, defend...) '+
      '· <b>unit formations</b> = form_* casts (testudo, shield wall - '+
      'unit-type dependent) · <b>unit abilities</b> live on the ABILITIES read '+
      'panel, <b>general abilities</b> on the GENERAL page · group formations '+
      '(column &amp;c) = out of '+
      'scope</span></div>'+
      '<div class="cg"><b>behaviour toggle (modes)</b>'+
      '<select id="behsel">'+opt(BEHAVIOURS)+'</select> '+
      '<button data-a="beh1">ON</button><button data-a="beh0">OFF</button></div>'+
      '<div class="cg"><b>width</b>'+
      '<button data-a="winc">WIDTH +</button><button data-a="wdec">WIDTH -</button></div>'+
      '<div class="cg"><b>set formation (form_* tier)</b>'+
      '<input id="abil" size="18" placeholder="form_..."><button data-a="abil">FIRE</button>'+
      '<div id="abquick" style="margin-top:4px"></div></div>';
  if(kind==="w_meta")
    return '<div class="cg"><b>control ownership</b>'+
      '<button data-a="take">TAKE</button><button data-a="release">RELEASE</button> '+
      '<span class="hint">take = script owns the unit (orders work, game AI locked out); '+
      'release = hand it back</span></div>'+
      '<div class="cg"><b>reinforcement</b> deploy '+
      '<button data-a="tg:deployr:1">on</button><button data-a="tg:deployr:0">off</button> '+
      '<span class="hint">true test needs a battle with reinforcements due</span></div>'+
      '<div class="cg"><b>enable/disable</b> '+
      '<button data-a="tg:enabled:1">on</button><button data-a="tg:enabled:0">off</button> '+
      '<span class="hint">change_enabled: engine on/off switch for the unit - what it '+
      'visibly does is exactly what this test finds out</span></div>';
  return "";
}
function wireWidget(){
  var cvm=document.getElementById("map");
  if(cvm){
    cvm.onmousedown=function(e){
      var r=cvm.getBoundingClientRect(), sx=e.clientX-r.left, sy=e.clientY-r.top;
      var w0=mapFit?s2w(mapFit,sx,sy):[0,0];
      hdrag={btn:e.button,sx0:sx,sy0:sy,cx:sx,cy:sy,wx0:w0[0],wz0:w0[1],
             moved:false,alt:e.altKey,ctrl:e.ctrlKey};
      if(e.button===2) e.preventDefault();
    };
    cvm.onmousemove=function(e){
      var r=cvm.getBoundingClientRect(), sx=e.clientX-r.left, sy=e.clientY-r.top;
      mouse={x:e.clientX,y:e.clientY};
      if(hdrag){
        if(Math.hypot(sx-hdrag.sx0,sy-hdrag.sy0)>4) hdrag.moved=true;
        if(hdrag.btn===0&&hdrag.moved){
          hview.panx+=sx-hdrag.cx; hview.pany+=sy-hdrag.cy; }
        hdrag.cx=sx; hdrag.cy=sy;
        return;
      }
      hoverU=hitUnit(sx,sy);
      if(hoverU) showTip(hoverU);      // stats pop on CLICK now, never on hover
      else{ var hb=hitBld(sx,sy);      // buildings label on hover
        if(hb) showBldTip(hb); else showTip(null); }
    };
    cvm.onmouseleave=function(){ hoverU=null; showTip(null); };
    cvm.onmouseup=function(e){
      if(!hdrag) return; var d=hdrag; hdrag=null;
      var r=cvm.getBoundingClientRect(), sx=e.clientX-r.left, sy=e.clientY-r.top;
      var o=hitUnit(sx,sy);
      if(d.btn===0){
        if(d.moved) return;                        // was a pan
        if(o){                                     // unit: select (+target if AI)
          selUid=o.uid; if(o.key) setTarget(o.key);
          if(mapKind==="r_card") showCard(o.u);    // click opens the stat sheet
          updateTop(); renderDetail(); return;
        }
        if(mapFit){                                // ground: store pt (A/B verbs)
          var w=s2w(mapFit,sx,sy);
          clickPts.push([w[0],w[1]]);
          if(clickPts.length>2) clickPts.shift();
          updMapMsg();
        }
      } else if(d.btn===2){
        gestureOrder(d,sx,sy,o);
      }
    };
    cvm.oncontextmenu=function(e){ e.preventDefault(); };
    cvm.onwheel=function(e){
      e.preventDefault();
      if(!mapFit) return;
      var r=cvm.getBoundingClientRect(), sx=e.clientX-r.left, sy=e.clientY-r.top;
      var before=s2w(mapFit,sx,sy), f=e.deltaY<0?1.12:1/1.12;
      hview.zoom=Math.max(.3,Math.min(12,hview.zoom*f));
      var after=s2w(mapFit,sx,sy), S=mapFit.s*hview.zoom;
      hview.panx+=(after[0]-before[0])*S; hview.pany-=(after[1]-before[1])*S;
    };
    var rc=document.getElementById("runchk");
    if(rc){ rc.checked=runMode; rc.onchange=function(){ runMode=this.checked; }; }
  }
  var ct=document.getElementById("ctrl");
  if(ct) ct.onclick=function(e){
    var b=e.target.closest("button");
    if(b&&b.dataset.a) conAct(b.dataset.a);
  };
  var ts=document.getElementById("tsel");
  if(ts){ renderTsel(); ts.onchange=function(){ setTarget(this.value||null); }; }
  renderAbilQuick(); renderOrders(); renderDetail(); renderUlist();
}
function updMapMsg(){
  var el=document.getElementById("mapmsg");
  if(!el||(mapKind.charAt(0)!=="w"&&mapKind!=="r_terrain")) return;
  var t="left-click AI = target \u00b7 right-click = move \u00b7 "+
    "right-drag = draw the line (ghost shows placement) \u00b7 "+
    "right-click enemy = attack \u00b7 alt+right = atk ground \u00b7 "+
    "drag pan \u00b7 wheel zoom \u00b7 R run \u00b7 H halt";
  if(clickPts.length)
    t="pts: "+clickPts.map(function(p,i){
      return (clickPts.length>1?(i?"B ":"A "):"")+fx(p[0])+","+fx(p[1]);}).join("  ")+
      " \u00b7 left-click ground = add/replace";
  el.textContent=t;
}
function setTarget(k){
  targetKey=k||null;
  if(k) selUid="k:"+k;
  updateTop(); renderTsel(); renderDetail(); renderAbilQuick();
}

// ---- map ----
function fitMap(us,cw,ch){
  var b={minx:1e9,minz:1e9,maxx:-1e9,maxz:-1e9};
  us.forEach(function(o){var p=o.u.pos;if(!p)return;
    b.minx=Math.min(b.minx,p.x);b.maxx=Math.max(b.maxx,p.x);
    b.minz=Math.min(b.minz,p.z);b.maxz=Math.max(b.maxz,p.z);});
  if(b.minx>b.maxx) b={minx:-500,minz:-500,maxx:500,maxz:500};
  var pad=26,bw=Math.max(1,b.maxx-b.minx),bh=Math.max(1,b.maxz-b.minz);
  var s=Math.min((cw-2*pad)/bw,(ch-2*pad)/bh);
  return {s:s,cx:(b.minx+b.maxx)/2,cz:(b.minz+b.maxz)/2,cw:cw,ch:ch};
}
// pan/zoom state (cockpit-style: left-drag pan, wheel zoom)
var hview={zoom:1,panx:0,pany:0};
var hdrag=null, runMode=false;
function w2s(f,x,z){var S=f.s*hview.zoom;
  return [f.cw/2+hview.panx+(x-f.cx)*S, f.ch/2+hview.pany-(z-f.cz)*S];}
function s2w(f,sx,sy){var S=f.s*hview.zoom;
  return [(sx-f.cw/2-hview.panx)/S+f.cx, -(sy-f.ch/2-hview.pany)/S+f.cz];}
// right-drag = front line, game convention: drag left->right => unit faces AWAY
// (facing = drag direction rotated 90 deg CCW seen top-down, exactly like Attila)
function orientPlan(pw,rw){
  var dx=rw[0]-pw[0], dz=rw[1]-pw[1], len=Math.hypot(dx,dz);
  if(len<3) return null;
  var along=[dx/len,dz/len], facing=[-along[1],along[0]];
  var bearing=((Math.atan2(facing[0],facing[1])*180/Math.PI)%360+360)%360;
  return {bearing:bearing,len:len,mid:[pw[0]+dx/2,pw[1]+dz/2],facing:facing};
}
// unit footprint in world metres: frontage + rank depth from men count / class
// (live width() is nil in this build; ordered_width is live -> best fallback)
function footprint(u,front){
  var men=(u&&(u.men||u.men0))||40, cav=u&&u.cavalry, art=u&&u.arty;
  var sp=cav?1.9:(art?2.6:1.4), rd=cav?2.6:(art?3.2:1.6);
  var W=front||(u&&u.width>0?u.width:0)||(u&&u.owidth>0?u.owidth:0);
  if(!W){ var ratio=cav?2.0:(art?1.5:4.0),
    area=Math.max(20,men*(cav?2.4:(art?3.0:1.2)));
    W=Math.sqrt(area*ratio); }
  var files=Math.max(1,Math.round(W/sp)), ranks=Math.max(1,Math.ceil(men/files));
  return {front:W,hw:Math.max(2,W/2),hd:Math.max(1.5,ranks*rd/2),
          files:files,ranks:ranks,sp:sp};
}
function frontClamp(u){ // drag width limits: 4-file minimum .. single-rank maximum
  var men=(u&&(u.men||u.men0))||40, sp=(u&&u.cavalry)?1.9:1.4;
  return {min:Math.max(4,4*sp),max:Math.max(8,men*sp)};
}
function targetUnit(){
  var t=null;
  if(targetKey) allUnits().forEach(function(o){ if(o.key===targetKey) t=o; });
  return t;
}
var lastGhost=null;   // fading placement ghost after an order fires
// the placement ghost: the unit as it will stand (gold rect, bright front edge,
// soldier dots when zoomed) -- replaces the old dashed-line geometry readout
function drawGhostH(g,mid,facing,front,u,alpha){
  var S=mapFit.s*hview.zoom, c=w2s(mapFit,mid[0],mid[1]);
  var f=footprint(u,front), br=Math.atan2(facing[0],facing[1]);
  var hw=Math.max(5,f.hw*S), hd=Math.max(3,f.hd*S);
  g.save(); g.globalAlpha=alpha;
  g.translate(c[0],c[1]); g.rotate(br);
  g.fillStyle="rgba(255,215,94,.18)"; g.fillRect(-hw,-hd,2*hw,2*hd);
  g.strokeStyle="rgba(255,215,94,.85)"; g.lineWidth=1.2;
  g.strokeRect(-hw,-hd,2*hw,2*hd);
  g.lineWidth=3; g.beginPath();                    // bright front edge
  g.moveTo(-hw,-hd); g.lineTo(hw,-hd); g.stroke();
  g.lineWidth=1.2; g.beginPath();                  // facing tick
  g.moveTo(0,-hd); g.lineTo(0,-hd-8); g.stroke();
  var men=(u&&(u.men||u.men0))||f.files*f.ranks;
  if(f.sp*S>=3&&f.files*f.ranks<=1200){            // soldier dots at close zoom
    g.fillStyle="rgba(255,215,94,.7)";
    for(var rr=0;rr<f.ranks;rr++)for(var q=0;q<f.files;q++){
      if(rr*f.files+q>=men) break;
      g.beginPath();
      g.arc(-hw+(q+0.5)*(2*hw/f.files),-hd+(rr+0.5)*(2*hd/f.ranks),
            Math.max(1,S*0.42),0,7);
      g.fill();
    }
  }
  g.restore(); g.globalAlpha=1;
  return f;
}
// right-click order dispatch, game semantics, on the harness target unit
function gestureOrder(d,sx,sy,o){
  if(!targetKey){ toast("left-click an AI unit to target it first"); return; }
  if(!mapFit) return;
  var w=s2w(mapFit,sx,sy), tu=targetUnit(), u=tu?tu.u:null;
  if(d.moved){
    var pl=orientPlan([d.wx0,d.wz0],w);
    if(pl){
      var cl=frontClamp(u), front=Math.max(cl.min,Math.min(cl.max,pl.len));
      lastGhost={t:Date.now(),mid:pl.mid,facing:pl.facing,front:front,u:u};
      fire("form "+targetKey+" "+fx(pl.mid[0])+" "+fx(pl.mid[1])+" "+
        Math.round(pl.bearing)+" "+fx(front)+" "+(runMode?1:0)); return; }
  }
  clickPts.push([w[0],w[1]]); if(clickPts.length>2) clickPts.shift(); updMapMsg();
  if(o&&o.side==="player"&&o.u.pos){
    fire("aunit "+targetKey+" "+fx(o.u.pos.x)+" "+fx(o.u.pos.z)); return;
  }
  if(d.alt){ fire("apos "+targetKey+" "+fx(w[0])+" "+fx(w[1])+" "+(runMode?1:0)); return; }
  if(u&&u.pos){                    // plain move: ghost faces the travel direction
    var mx=w[0]-u.pos.x, mz=w[1]-u.pos.z, ml=Math.hypot(mx,mz)||1;
    lastGhost={t:Date.now(),mid:[w[0],w[1]],facing:[mx/ml,mz/ml],
               front:footprint(u,null).front,u:u};
  }
  fire("move "+targetKey+" "+fx(w[0])+" "+fx(w[1])+" "+(runMode?1:0));
}
function syncRun(){
  var c=document.getElementById("runchk"); if(c) c.checked=runMode;
  updateTop();
}
window.addEventListener("keydown",function(e){
  if(e.target&&/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if(e.key==="r"||e.key==="R"){ runMode=!runMode; syncRun(); }
  else if(e.key==="Escape"){ targetKey=null; selUid=null;
    updateTop(); renderTsel(); renderDetail(); }
  else if((e.key==="h"||e.key==="H")&&targetKey){ fire("halt "+targetKey); }
});
function hitUnit(sx,sy){
  if(!mapFit) return null;
  var S=mapFit.s*hview.zoom, best=null,bd=1e9;
  allUnits().forEach(function(o){var p=o.u.pos;if(!p)return;
    var c=w2s(mapFit,p.x,p.z), d=Math.hypot(c[0]-sx,c[1]-sy);
    var r=Math.max(9,footprint(o.u,null).hw*S);
    if(d<r&&d<bd){bd=d;best=o;}});
  return best;
}
function cross(g,c,label){
  g.strokeStyle="#d9b45b"; g.lineWidth=1.4;
  g.beginPath(); g.moveTo(c[0]-7,c[1]); g.lineTo(c[0]+7,c[1]);
  g.moveTo(c[0],c[1]-7); g.lineTo(c[0],c[1]+7); g.stroke(); g.lineWidth=1;
  if(label){ g.fillStyle="#d9b45b"; g.font="10px monospace";
    g.fillText(label,c[0]+8,c[1]-4); }
}
function drawMap(){
  requestAnimationFrame(drawMap);
  var cvm=document.getElementById("map"); if(!cvm) return;
  if(cvm.width!==cvm.clientWidth||cvm.height!==cvm.clientHeight){
    cvm.width=cvm.clientWidth; cvm.height=cvm.clientHeight; }
  var g=cvm.getContext("2d");
  g.clearRect(0,0,cvm.width,cvm.height);
  var us=allUnits();
  mapFit=fitMap(us,cvm.width,cvm.height);
  if(!us.length){
    g.fillStyle="#8797a8"; g.font="12px monospace"; g.textAlign="center";
    g.fillText("no battle feed \u2014 map idle",cvm.width/2,cvm.height/2);
    g.textAlign="start";
  }
  var flash=(Date.now()%700)<350, S=mapFit.s*hview.zoom;
  if(bldData&&bldData.rows&&bldData.rows.length){  // probed buildings layer,
    var br=Math.max(2.5,1.2*S);                    // under the units: navy =
    g.globalAlpha=.85;                             // allied, maroon = enemy,
    [["player","#2b4a9f"],["ai","#8b2635"],[null,"#e8b33e"]]  // amber = unowned
      .forEach(function(pc){                       // (batched by color: the
        g.fillStyle=pc[1];                         // full scan is 10k+ rows)
        bldData.rows.forEach(function(bd){
          if(bd.x==null||bd.z==null||bldSide(bd)!==pc[0]) return;
          var c=w2s(mapFit,bd.x,bd.z);
          g.fillRect(c[0]-br,c[1]-br,2*br,2*br);
        });
      });
    g.globalAlpha=1;
  }
  us.forEach(function(o){                       // game-style footprint rects
    var u=o.u, p=u.pos; if(!p) return;
    var c=w2s(mapFit,p.x,p.z);
    var f=footprint(u,null), br=(u.bearing||0)*Math.PI/180;
    var hw=Math.max(4,f.hw*S), hd=Math.max(2.5,f.hd*S);
    var col=(o.side==="player")?"#5aa2ff":"#ff5b5b";
    if((u.routing||u.shattered)&&flash) col="#ffd24a";
    g.save(); g.translate(c[0],c[1]); g.rotate(br);
    g.fillStyle=col; g.globalAlpha=(u.routing&&!flash)?0.5:0.9;
    g.fillRect(-hw,-hd,2*hw,2*hd); g.globalAlpha=1;
    g.strokeStyle="rgba(255,255,255,.8)"; g.lineWidth=1.5;
    g.beginPath(); g.moveTo(-hw,-hd); g.lineTo(hw,-hd); g.stroke();  // front edge
    g.beginPath(); g.moveTo(0,-hd); g.lineTo(0,-hd-5); g.stroke();   // facing tick
    if(o.uid===selUid||(o.key&&o.key===targetKey)){
      g.strokeStyle=(o.key&&o.key===targetKey)?"#ffd75e":"#fff";
      g.lineWidth=1.6; g.strokeRect(-hw-2.5,-hd-2.5,2*hw+5,2*hd+5);
    }
    g.lineWidth=1; g.restore();
    var bw=Math.max(12,2*hw), bx=c[0]-bw/2, by=c[1]-hd-9;
    var hp=(u.men0&&u.men!=null)?u.men/u.men0:1;
    g.fillStyle="#20262e"; g.fillRect(bx,by,bw,3);
    g.fillStyle=hp>.5?"#4ade80":hp>.25?"#facc15":"#ef4444";
    g.fillRect(bx,by,bw*Math.max(0,Math.min(1,hp)),3);
    if(u.ammo0){ var am=(u.ammo||0)/u.ammo0;
      g.fillStyle="#20262e"; g.fillRect(bx,by-4,bw,2);
      g.fillStyle="#60a5fa"; g.fillRect(bx,by-4,bw*Math.max(0,Math.min(1,am)),2); }
  });
  var so=selUnit();
  if(so&&so.u.pos){
    var c=w2s(mapFit,so.u.pos.x,so.u.pos.z), u=so.u;
    if(mapKind==="r_pos"){
      if(u.bearing!=null){                      // heading arrow
        var br=u.bearing*Math.PI/180, dx=Math.sin(br)*24, dy=-Math.cos(br)*24;
        g.strokeStyle="#d9b45b"; g.lineWidth=2;
        g.beginPath(); g.moveTo(c[0],c[1]); g.lineTo(c[0]+dx,c[1]+dy); g.stroke();
        var ang=Math.atan2(dy,dx);
        g.beginPath(); g.moveTo(c[0]+dx,c[1]+dy);
        g.lineTo(c[0]+dx-8*Math.cos(ang-0.4),c[1]+dy-8*Math.sin(ang-0.4));
        g.lineTo(c[0]+dx-8*Math.cos(ang+0.4),c[1]+dy-8*Math.sin(ang+0.4));
        g.closePath(); g.fillStyle="#d9b45b"; g.fill(); g.lineWidth=1;
      }
      if(u.officer){                            // officer dot
        var oc=w2s(mapFit,u.officer.x,u.officer.z);
        g.fillStyle="#fff"; g.beginPath(); g.arc(oc[0],oc[1],2.2,0,7); g.fill();
      }
      if(measureUid){                           // distance-tool line
        var mo2=null;
        allUnits().forEach(function(m){ if(m.uid===measureUid) mo2=m; });
        if(mo2&&mo2.u.pos){
          var mc=w2s(mapFit,mo2.u.pos.x,mo2.u.pos.z);
          g.strokeStyle="#c9a2ff"; g.setLineDash([4,4]);
          g.beginPath(); g.moveTo(c[0],c[1]); g.lineTo(mc[0],mc[1]); g.stroke();
          g.setLineDash([]);
          g.strokeStyle="#c9a2ff";
          g.beginPath(); g.arc(mc[0],mc[1],6,0,7); g.stroke();
          var md2=Math.hypot(mo2.u.pos.x-u.pos.x,mo2.u.pos.z-u.pos.z);
          g.fillStyle="#c9a2ff"; g.font="11px Consolas,monospace";
          g.fillText(Math.round(md2)+" m",(c[0]+mc[0])/2+6,(c[1]+mc[1])/2-4);
        }
      }
    }
    if(mapKind==="r_orders"&&u.ordered){        // ordered-destination line
      var oc2=w2s(mapFit,u.ordered.x,u.ordered.z);
      g.strokeStyle="#7dd3fc"; g.setLineDash([5,4]);
      g.beginPath(); g.moveTo(c[0],c[1]); g.lineTo(oc2[0],oc2[1]); g.stroke();
      g.setLineDash([]);
      g.strokeStyle="#7dd3fc";
      g.beginPath(); g.arc(oc2[0],oc2[1],5,0,7); g.stroke();
    }
  }
  if(hdrag&&hdrag.btn===2&&hdrag.moved&&mapFit&&targetKey){
    // right-drag: live placement ghost -- the unit exactly as it will stand
    var wb=s2w(mapFit,hdrag.cx,hdrag.cy);
    var pl=orientPlan([hdrag.wx0,hdrag.wz0],wb), tu=targetUnit();
    if(pl&&tu){
      var cl=frontClamp(tu.u), front=Math.max(cl.min,Math.min(cl.max,pl.len));
      var fg=drawGhostH(g,pl.mid,pl.facing,front,tu.u,1);
      g.fillStyle="#ffd75e"; g.font="11px Consolas,monospace";
      g.fillText(Math.round(front)+" m · "+fg.ranks+" ranks",
        hdrag.cx+12,hdrag.cy-10);
    }
  }
  if(lastGhost&&mapFit){                          // fading ghost after the order
    var age=Date.now()-lastGhost.t;
    if(age>1100) lastGhost=null;
    else drawGhostH(g,lastGhost.mid,lastGhost.facing,lastGhost.front,
                    lastGhost.u,1-age/1100);
  }
  if(mapKind.charAt(0)==="w"||mapKind==="r_terrain"){
    clickPts.forEach(function(pt,i){
      cross(g,w2s(mapFit,pt[0],pt[1]),clickPts.length>1?(i?"B":"A"):"");
    });
    if(clickPts.length===2){
      var a=w2s(mapFit,clickPts[0][0],clickPts[0][1]),
          b=w2s(mapFit,clickPts[1][0],clickPts[1][1]);
      g.strokeStyle="rgba(217,180,91,.6)"; g.setLineDash([4,3]);
      g.beginPath(); g.moveTo(a[0],a[1]); g.lineTo(b[0],b[1]); g.stroke();
      g.setLineDash([]);
    }
  }
  if(hoverU&&hoverU.u.pos){
    var hc=w2s(mapFit,hoverU.u.pos.x,hoverU.u.pos.z);
    g.strokeStyle="rgba(255,255,255,.5)";
    g.beginPath(); g.arc(hc[0],hc[1],9,0,7); g.stroke();
  }
}

// ---- tooltip + unit card ----
function showTip(o){
  var el=document.getElementById("tip");
  if(!o){el.style.display="none";return;}
  var u=o.u;
  el.innerHTML='<b style="color:var(--gold)">'+esc(u.name||"?")+'</b> '+esc(u.type||"")+
    '<br>'+esc(u.cls||"")+' &middot; '+(o.side==="player"?"player":"AI")+
    (o.key?' &middot; key '+esc(o.key):"")+
    '<br>men '+(u.men!=null?u.men:"?")+"/"+(u.men0!=null?u.men0:"?")+
    (u.routing?' &middot; <span style="color:var(--red)">ROUTING</span>':"");
  el.style.display="block";
  el.style.left=Math.min(mouse.x+14,window.innerWidth-275)+"px";
  el.style.top=Math.min(mouse.y+12,window.innerHeight-90)+"px";
}
// building hover: which probed piece is under the cursor (screen-space)
function hitBld(sx,sy){
  if(!bldData||!bldData.rows||!mapFit) return null;
  var S=mapFit.s*hview.zoom, best=null, bestD=1e9;
  bldData.rows.forEach(function(bd){
    if(bd.x==null||bd.z==null) return;
    var c=w2s(mapFit,bd.x,bd.z);
    var d=Math.hypot(c[0]-sx,c[1]-sy), r=Math.max(2.5,1.2*S)+4;
    if(d<r&&d<bestD){ best=bd; bestD=d; }
  });
  return best;
}
function bldSide(bd){        // owner id vs the player's alliance
  if(bd.owner==null||bd.owner<0) return null;      // -1 = unowned (measured
  return (state&&bd.owner===state.player_alliance)?"player":"ai";  // 07-31)
}
function showBldTip(bd){
  var el=document.getElementById("tip");
  if(!bd){el.style.display="none";return;}
  var sd=bldSide(bd);
  el.innerHTML='<b style="color:var(--gold)">'+esc(bd.name||"building")+'</b>'+
    ' <span class="hint">#'+bd.i+'</span><br>'+
    (bd.health!=null?'hp '+fx(bd.health):'hp ?')+
    (sd===null?' &middot; owner unknown':
      ' &middot; '+(sd==="player"?'allied':'enemy'))+
    (bd.garr?' &middot; GARRISONED':'')+
    (bd.cap!=null?' &middot; cap '+bd.cap:'');
  el.style.display="block";
  el.style.left=Math.min(mouse.x+14,window.innerWidth-275)+"px";
  el.style.top=Math.min(mouse.y+12,window.innerHeight-90)+"px";
}
// the uniform DB stat sheet: EVERY group and EVERY field renders for EVERY
// unit ("—" where a stat doesn't apply to the type) so a given stat always
// sits in the same place while comparing units. Shared by the hover card
// and the unit-card panel.
function statSheetHtml(s){
  var hb="";
  if(s){
    // the in-game card shows COMPOSITES (DB_DATA.md): base + shield, dmg + ap.
    // campaign veterancy/tech stack on top, so live cards can exceed these.
    var comp=function(lab,a,b,tag){
      a=nz(a); b=nz(b);
      return '<div class="row"><span>'+lab+'</span><span>'+(a+b)+
        ' <span class="gv">('+a+' + '+b+' '+tag+')</span></span></div>';
    };
    hb+='<div class="shdr">as the game card shows it (no campaign ranks)</div>'+
      '<div class="row"><span>melee attack</span><span>'+nz(s.melee_attack)+'</span></div>'+
      comp("melee defense",s.melee_defence,s.shield_defence,"shield")+
      comp("melee damage",s.weapon_damage,s.weapon_ap_damage,"ap")+
      '<div class="row"><span>charge bonus</span><span>'+nz(s.charge_bonus)+'</span></div>'+
      comp("armour",s.armour,s.shield_armour,"shield")+
      (s.has_missile?comp("missile damage",s.missile_damage,s.missile_ap_damage,"ap"):"");
  }
  CARD_GROUPS.forEach(function(gr){
    var rows="";
    gr[1].forEach(function(fd){
      var v=s?s[fd[0]]:null;
      rows+='<div class="row"><span>'+fd[1]+'</span><span'+
        (v==null?' class="gv"':'')+'>'+(v==null?"&mdash;":esc(v))+'</span></div>';
    });
    hb+='<div class="shdr">'+gr[0]+'</div>'+rows;
  });
  hb+='<div class="shdr">attributes</div><div class="hint">'+
    ((s&&s.attributes&&s.attributes.length)?esc(s.attributes.join(", ")):"&mdash;")+'</div>'+
    '<div class="shdr">abilities</div><div class="hint">'+
    ((s&&s.abilities&&s.abilities.length)?esc(s.abilities.map(abName).join(", ")):"&mdash;")+'</div>';
  return hb;
}
function showCard(u){
  var el=document.getElementById("ucard");
  if(!u||mapKind!=="r_card"){el.style.display="none";return;}
  var s=stats[u.type]||null;
  var h='<h3>'+esc(u.name||"?")+' &middot; '+esc(u.type||"?")+'</h3>'+
    '<div class="hint">'+esc(u.cls||"")+(u.key?' &middot; key '+esc(u.key):' &middot; player side')+
    (s?"":' &middot; <span style="color:var(--amber)">no DB entry</span>')+'</div>'+
    barHtml("men",u.men,u.men0,"#4ade80")+
    (u.ammo0?barHtml("ammo",u.ammo,u.ammo0,"#60a5fa"):"");
  el.innerHTML='<span id="ucx" style="float:right;cursor:pointer;'+
    'color:var(--dim);padding:0 6px;font-size:14px">&#10005;</span>'+
    h+'<div class="cbody">'+statSheetHtml(s)+'</div>';
  el.style.display="block";
  var x=document.getElementById("ucx");
  if(x) x.onclick=function(){ el.style.display="none"; };
}

// ---- detail panel (READ modes) ----
function nz(x){var v=(typeof x==="number")?x:parseFloat(x);return isNaN(v)?0:v;}
function kv(k,v){return '<div class="row"><span>'+k+'</span><span>'+v+'</span></div>';}
function badge(label,on,tip){return '<span class="bdg'+(on?" on":"")+'"'+
  (tip?' title="'+esc(tip)+'"':'')+'>'+esc(label)+'</span>';}
function barHtml(label,v,mx,color){
  var f=(mx>0&&v!=null)?Math.max(0,Math.min(1,v/mx)):0;
  return '<div class="brow"><span>'+esc(label)+'</span>'+
    '<span class="bwrap"><i style="width:'+Math.round(f*100)+'%;background:'+color+
    '"></i></span><span>'+(v!=null?v:"?")+"/"+(mx!=null?mx:"?")+'</span></div>';
}
function renderDetail(){
  var el=document.getElementById("dtl"); if(!el) return;
  if(mapKind!=="r_card") showCard(null);   // the sheet lives on r_card only
  if(!state){
    el.innerHTML='<div class="hint">no battle feed \u2014 live reads need a running '+
      'battle; the checklist below still works</div>';
    return;
  }
  if(mapKind==="r_flow"){
    var hf='<div class="shdr">battle</div>'+
      kv("phase",esc(state.phase||"?"))+
      kv("battle clock (our ticks)",state.t!=null?Math.round(state.t)+" s":"?")+
      kv("engine time remaining",state.remaining!=null?
        Math.round(state.remaining)+" s":"— (needs current pack)")+
      kv("battle over",state.over===true?"YES":(state.over===false?"no":"?"))+
      kv("player alliance",state.player_alliance!=null?state.player_alliance:"?");
    if(state.camera){
      var cp=state.camera.pos, ctg=state.camera.target;
      hf+='<div class="shdr">camera</div>'+
        kv("pos",cp?fx(cp.x)+", "+fx(cp.y)+", "+fx(cp.z):"?")+
        kv("target",ctg?fx(ctg.x)+", "+fx(ctg.z):"?");
    }
    (state.alliances||[]).forEach(function(al,ai){
      (al.armies||[]).forEach(function(ar,ri){
        hf+='<div class="shdr">alliance '+(ai+1)+' &middot; army '+(ri+1)+'</div>'+
          kv("units",ar.n!=null?ar.n:(ar.units||[]).length)+
          kv("commander alive",ar.commander_alive===false?"NO":
            (ar.commander_alive?"yes":"?"))+
          kv("reinforcements",(ar.reinf&&ar.reinf.length)?
            ar.reinf.length+" waiting":"none waiting")+
          kv("ships",(ar.ships!=null)?ar.ships:"—")+
          kv("reinf ships",(ar.reinf_ships!=null)?ar.reinf_ships:"—");
      });
    });
    hf+='<div class="hint" style="margin-top:6px">reinforcements = armies/fleets that '+
      'arrive mid-battle (adjacent on the campaign map when the battle started); '+
      '"waiting" counts reserves not yet on the field \u2014 once one marches on, it joins '+
      'the army rows above. Ships rows: 0 on land, live in naval battles '+
      '(SHIPS PROBE below re-reads them on demand)</div>';
    el.innerHTML=hf;
    return;
  }
  if(mapKind==="r_terrain"){
    var ht='<div class="shdr">elevation point sample</div>'+
      '<div class="hint">click the map, hit ELEV SAMPLE below - the height comes '+
      'back as OK = &lt;value&gt; on the ack line. March a unit up a hill and its '+
      'pos y (position panel) is the same read.</div>';
    var uy=[];
    allUnits().forEach(function(m){
      if(m.u.pos&&uy.length<10)
        uy.push(kv(esc(m.u.name||"?"),"y = "+fx(m.u.pos.y)));
    });
    if(uy.length) ht+='<div class="shdr">live unit elevations</div>'+uy.join("");
    el.innerHTML=ht;
    return;
  }
  if(mapKind==="r_bld"){
    var hbld='<div class="shdr">buildings</div>';
    if(!bldData||!bldData.rows)
      hbld+='<div class="hint">nothing scanned yet - hit SCAN NEXT below; '+
        'each click adds the next chunk to the map. '+
        'Open field = 0 or a few; real data needs a siege.</div>';
    else{
      var done=bldData.total!=null&&bldData.scanned>=bldData.total;
      hbld+=kv("scan progress",(bldData.scanned!=null?bldData.scanned:"?")+" / "+
          (bldData.total!=null?bldData.total:"?")+(done?" ✓ done":""))+
        kv("buildings kept",bldData.kept!=null?bldData.kept:bldData.rows.length);
      if(bldData.cold_at!=null)
        hbld+='<div class="hint" style="color:var(--amber)">COLD STOP at entry '+
          bldData.cold_at+' - the scan hit a cold slot. Check the feed: one '+
          'cold touch is usually fatal to it. Verdict 07-31: cold slots are '+
          'undetectable from Lua (cold metatable = warm metatable) and even '+
          'name-only touches kill - the Lua scan route is closed; buildings '+
          'continue on the native/static route.</div>';
      hbld+=
        '<div class="hint">'+(done?'full registry walked - the next click '+
        'restarts the scan fresh. ':'mid-scan: SCAN NEXT below continues. ')+
        'Kept = the architecture (corpses + ground tiles filtered out). On the '+
        'map: <span style="color:#5a7ce0">navy</span> = allied, '+
        '<span style="color:#c04a5a">maroon</span> = enemy, '+
        '<span style="color:var(--amber)">amber</span> = unowned; hover a square '+
        'for its name, hp and #idx (what the write verbs take)</div>';
    }
    el.innerHTML=hbld;
    return;
  }
  if(mapKind==="r_vp"){
    var hvp='<div class="shdr">capture events</div>';
    if(!capevData||!capevData.rows||!capevData.rows.length)
      hvp+='<div class="hint">none yet - they fire when a plaza/fort capture '+
        'starts or completes (needs a fort/siege battle with capture points)</div>';
    else{
      var lce=capevData.rows[capevData.rows.length-1];
      hvp+=kv("events seen",capevData.rows.length)+
        kv("latest",esc(lce.nm)+" at clock "+fx(lce.clock));
    }
    el.innerHTML=hvp;
    return;
  }
  var o=selUnit();
  if(!o){
    el.innerHTML='<div class="hint">click a unit on the map to inspect it</div>'+
      (mapKind==="r_abil"?abilBoardHtml():"")+
      (mapKind==="r_stance"?formBoardHtml():"")+
      (mapKind==="r_gabil"?genBoardHtml():"");
    return;
  }
  var u=o.u;
  var h='<h3 style="margin:0;color:var(--gold);font-size:13px">'+esc(u.name||"?")+'</h3>'+
    '<div class="hint">'+esc(u.type||"?")+
    (o.key?' &middot; key '+esc(o.key):' &middot; player side')+'</div>';
  if(mapKind==="r_id"){
    h+='<div class="shdr">identity</div>'+
      kv("type",esc(u.type||"?"))+kv("name",esc(u.name||"?"))+
      kv("class",esc(u.cls||"?"))+
      '<div class="shdr">archetype</div>'+
      badge("infantry",u.inf)+badge("cavalry",u.cavalry)+
      badge("artillery",u.arty)+badge("on ships",!!u.naval)+badge("dismounted ships",!!u.dships);
  } else if(mapKind==="r_pos"){
    h+='<div class="shdr">position</div>'+
      kv("pos",u.pos?fx(u.pos.x)+", "+fx(u.pos.y)+", "+fx(u.pos.z):"?")+
      kv("bearing",u.bearing!=null?Math.round(u.bearing)+" deg (arrow)":"?")+
      kv("ordered width",u.owidth!=null?fx(u.owidth):"\u2014")+
      kv("officer",u.officer?fx(u.officer.x)+", "+fx(u.officer.z)+" (white dot)":"\u2014")+
      '<div class="shdr">movement</div>'+
      badge("moving",u.moving)+badge("idle",u.idle)+badge("running",u.fast);
    h+='<div class="shdr">distance tool</div>';
    var us2=allUnits(), opts='<option value="">measure to\u2026</option>', mo=null;
    us2.forEach(function(m){
      if(m.uid===o.uid) return;
      if(m.uid===measureUid) mo=m;
      opts+='<option value="'+esc(m.uid)+'"'+(m.uid===measureUid?' selected':'')+'>'+
        (m.side==="player"?"[P] ":"[AI] ")+esc(m.u.name||"?")+' \u00b7 '+esc(m.u.type||"")+
        '</option>';
    });
    h+='<select id="msel" style="width:100%;margin:4px 0;background:#0d1420;'+
      'color:#dfe7f2;border:1px solid #2a3a4e;border-radius:4px;padding:4px">'+
      opts+'</select>';
    if(mo&&mo.u.pos&&u.pos){
      var md=Math.hypot(mo.u.pos.x-u.pos.x,mo.u.pos.z-u.pos.z);
      h+=kv("distance",fx(md)+" m (violet line)")+
        kv("missile range",u.range?Math.round(u.range)+" m":"melee unit")+
        kv("within range",u.range?(md<=u.range?"YES":"no"):"\u2014")+
        '<div class="hint">straight-line from feed positions; the engine\u2019s own '+
        'unit_distance()/unit_in_range() gave 602.3 m / false in the probe battle</div>';
    }
  } else if(mapKind==="r_combat"){
    h+='<div class="shdr">resources</div>'+
      barHtml("men",u.men,u.men0,"#4ade80")+
      (u.ammo0?barHtml("ammo",u.ammo,u.ammo0,"#60a5fa"):kv("ammo","none (melee)"))+
      kv("kills",u.kills!=null?u.kills:"\u2014")+
      kv("range",u.range?Math.round(u.range):"melee")+
      '<div class="shdr">flags</div>'+
      badge("routing",u.routing)+badge("shattered",u.shattered)+
      badge("leaving",u.leaving)+badge("under fire",u.under_fire)+
      badge("hidden",u.hidden)+badge("valid tgt",u.valid_tgt)+
      badge("garrisoned",u.garrisoned)+badge("visible",u.vis!==false);
  } else if(mapKind==="r_stance"){
    var st=u.stances||[];
    h+='<div class="shdr">modes (universal behaviour toggles - the two starred '+
      'ones reflect UI toggles too)</div>'+
      badge("fire at will *",u.faw)+badge("loose spacing *",u.loose);
    STANCE_SET.forEach(function(b){ h+=badge(b,st.indexOf(b)>=0); });
    h+='<div class="hint">the names below are the ENGINE’S own toggle '+
      'vocabulary (binary parse table) · proven: fire-at-will, loose '+
      '(= change_formation_spacing), skirmish · candidates to A/B: defend '+
      '(= the hold-ground toggle), formed_attack, dismount (cav), unlimber '+
      '(artillery), release_animals, drop_siege_equipment, board_ship (naval) '+
      '· hiding is PASSIVE - attributes on the unit card, state = the hidden '+
      'badge (combat panel) · shield wall / phalanx tier = form_* casts '+
      '(formations section below)</div>';
    var fsst=stats[u.type];
    var fforms=((fsst&&fsst.abilities)||[]).filter(function(a){
      return a.indexOf("form_")===0;});
    h+='<div class="shdr">formations (this unit&#39;s roster)</div>';
    if(!fforms.length){
      h+='<div class="hint">none for this unit type</div>';
    } else {
      fforms.forEach(function(f){ h+=badge(abName(f),u.ability===f,abTip(f)); });
      h+='<div class="hint">hover a badge for its effect · lit = active (stays dark '+
        'for now - the active-formation read is native-tier, not built)</div>';
    }
    if(o.key){
      h+='<div class="shdr">script-set &amp; watch the badge (the definitive test)</div>';
      BEHAVIOURS.forEach(function(b){
        h+='<div class="row"><span>'+esc(b)+'</span><span>'+
          '<button data-fire="beh '+esc(o.key)+' '+esc(b)+' 1">on</button> '+
          '<button data-fire="beh '+esc(o.key)+' '+esc(b)+' 0">off</button></span></div>';
      });
      if(fforms.length){
        h+='<div class="shdr">set formation (fires the form_* cast)</div>';
        fforms.forEach(function(a){
          h+='<div class="row"><span>'+esc(abName(a))+'</span><span>'+
            '<button data-fire="abil '+esc(o.key)+' '+esc(a)+'">fire</button></span></div>';
        });
      }
      h+='<div id="olog"></div>';
    } else {
      h+='<div class="hint" style="margin-top:6px">select an AI unit (red dot) to get '+
        'script-set buttons here — player units can’t be commanded from script '+
        'while you own them</div>';
    }
  } else if(mapKind==="r_orders"){
    if(u.ordered&&u.pos){
      var d=Math.hypot(u.ordered.x-u.pos.x,u.ordered.z-u.pos.z);
      h+='<div class="shdr">standing order</div>'+
        kv("ordered pos",fx(u.ordered.x)+", "+fx(u.ordered.z)+" (line)")+
        kv("distance",fx(d));
    } else h+='<div class="shdr">standing order</div>'+kv("ordered pos","\u2014");
    h+=kv("ordered width",u.owidth!=null?fx(u.owidth):"\u2014");
  } else if(mapKind==="r_abil"){
    h+='<div class="shdr">live</div>'+
      badge("has ability",u.has_ability)+badge("can perform",u.can_ability)+
      kv("current",u.ability?esc(abName(u.ability)):"none");
    var s=stats[u.type];
    var ros=(s&&s.abilities)||[];
    var racts=ros.filter(function(a){return a.indexOf("form_")!==0;});
    function rosRows(list){
      list.forEach(function(a){
        var inf=abinfo&&abinfo.abilities&&abinfo.abilities[a];
        h+='<div class="row"><span>'+esc(abName(a))+'</span><span>'+
          (o.key?'<button data-fire="abil '+esc(o.key)+' '+esc(a)+'">fire</button>':'')+
          '</span></div>'+
          '<div class="hint" style="margin:0 0 4px 10px">'+
          (inf?esc(abilSummary(inf)):"no params in the extract")+'</div>';
      });
    }
    if(!ros.length) h+='<div class="shdr">DB roster</div>'+
      '<div class="hint">none in the extract</div>';
    if(racts.length){
      h+='<div class="shdr">unit abilities (this unit&#39;s roster)</div>';
      racts.forEach(function(a){ h+=badge(abName(a),u.ability===a,abTip(a)); });
      h+='<div class="hint">hover a badge for its effect · lit = active (dark '+
        'unless the current-ability read lands)</div>';
      rosRows(racts);
    }
    if(o.key) h+='<div id="olog"></div>';
    else h+='<div class="hint" style="margin-top:6px">select an AI unit to get FIRE '+
      'buttons (script-cast); casting from the game UI is the other surface</div>';
  } else if(mapKind==="r_gabil"){
    // general abilities live ONLY here, and only on the general's unit —
    // army slot 1 (Theo-confirmed: the general is always the army's first
    // unit; bodyguards are full-size regular types, only the slot marks him)
    if(u.i===1){
      h+='<div class="shdr">general abilities</div>'+
        '<div class="hint">rally x5 star-levels, enabled per-general by his '+
        'command stars; this is the general&#39;s unit (army slot 1)</div>';
      GEN_ABILS.forEach(function(a){ h+=badge(abName(a),u.ability===a,abTip(a)); });
      h+='<div class="hint">hover a badge for its effect · lit = active (dark '+
        'unless the active-state read lands)</div>';
      GEN_ABILS.forEach(function(a){
        var inf=abinfo&&abinfo.abilities&&abinfo.abilities[a];
        h+='<div class="row"><span>'+esc(abName(a))+'</span><span>'+
          (o.key?'<button data-fire="abil '+esc(o.key)+' '+esc(a)+'">fire</button>':'')+
          '</span></div>'+
          '<div class="hint" style="margin:0 0 4px 10px">'+
          (inf?esc(abilSummary(inf)):"")+'</div>';
      });
      if(o.key) h+='<div id="olog"></div>';
    } else {
      h+='<div class="hint">this unit isn&#39;t the general - the general is '+
        'army slot 1; click unit 1 of either army</div>';
    }
  } else if(mapKind==="r_card"){
    h+='<div class="hint">click a unit (map or list) - the full stat sheet '+
      'pops up and SCROLLS (&#10005; closes it): the SAME fields for every '+
      'unit, &mdash; where a stat doesn&#39;t apply to that type</div>';
  } else {
    h+='<div class="shdr">live scalars</div>'+
      kv("fatigue",u.fatigue!=null?Math.round(u.fatigue*100)+"% (native)":
        "\u2014 (needs aai_native.dll)")+
      '<div class="hint" style="margin-top:6px">morale / experience / ship damage '+
      'are T3 native reads \u2014 not in the Lua feed</div>';
  }
  el.innerHTML=h;
  var ms=document.getElementById("msel");
  if(ms) ms.onchange=function(){
    measureUid=this.value||null; renderDetail(); drawMap();
  };
  el.querySelectorAll("[data-fire]").forEach(function(b){
    b.onclick=function(){ fire(b.dataset.fire); };
  });
  renderOrders();     // the panel re-renders each poll; re-fill the status strip
}
// compact one-line summary of an ability's DB params + phase effects
function abilSummary(inf){
  var bits=[];
  Object.keys(inf).forEach(function(k){
    var v=inf[k];
    if(k==="phases"||v==null||typeof v==="object") return;
    bits.push(k+" "+v);
  });
  (inf.phases||[]).forEach(function(p,i){
    var eb=[];
    Object.keys(p).forEach(function(k){
      if(k!=="effects"&&p[k]!=null&&typeof p[k]!=="object") eb.push(k+" "+p[k]);
    });
    (p.effects||[]).forEach(function(e){
      if(e&&typeof e==="object")
        eb.push(Object.keys(e).map(function(kk){return e[kk];}).join(" "));
      else eb.push(String(e));
    });
    if(eb.length) bits.push("phase "+(i+1)+": "+eb.join(", "));
  });
  return bits.join(" · ")||"(empty entry)";
}

// ---- unit list (unit-card segment) ----
function renderUlist(){
  var el=document.getElementById("ulist"); if(!el) return;
  var us=allUnits();
  var sig=us.map(function(o){return o.uid;}).join("|");
  if(sig===ulistSig) return;
  ulistSig=sig;
  if(!us.length){el.innerHTML='<div class="hint" style="padding:8px">no battle feed</div>';return;}
  el.innerHTML=us.map(function(o,i){
    return '<div class="ulrow" data-i="'+i+'"><span class="udot" style="background:'+
      (o.side==="player"?"#5aa2ff":"#ff5b5b")+'"></span><span>'+esc(o.u.name||"?")+
      '</span><span class="hint">'+esc(o.u.type||"")+'</span></div>';
  }).join("");
  el.querySelectorAll(".ulrow").forEach(function(r){
    r.onclick=function(){
      var o=allUnits()[+r.dataset.i]; if(!o) return;
      selUid=o.uid; if(o.key) setTarget(o.key);
      if(mapKind==="r_card") showCard(o.u);        // click opens the stat sheet
      updateTop(); renderDetail();
    };
  });
}

// ---- target selector + quick abilities ----
function renderTsel(){
  var s=document.getElementById("tsel"); if(!s) return;
  var ais=allUnits().filter(function(o){return o.key;});
  var sig=ais.map(function(o){return o.key;}).join(",");
  if(sig!==tselSig){
    tselSig=sig;
    s.innerHTML='<option value="">(pick AI unit)</option>'+ais.map(function(o){
      return '<option value="'+esc(o.key)+'">'+esc((o.u.name||"?")+" "+(o.u.type||""))+
        ' ['+esc(o.key)+']</option>';}).join("");
  }
  s.value=targetKey||"";
  var ti=document.getElementById("tinfo");
  if(ti){
    var o=targetKey?findKey(targetKey):null;
    ti.textContent=o?((o.u.cls||"")+" \u00b7 "+(o.u.men!=null?o.u.men:"?")+" men"):
      (ais.length?"":"no AI units in feed");
  }
}
function renderAbilQuick(){
  var el=document.getElementById("abquick"); if(!el) return;
  var o=targetKey?findKey(targetKey):null;
  var s=(o&&o.u.type)?stats[o.u.type]:null;
  var fl=((s&&s.abilities)||[]).filter(function(a){return a.indexOf("form_")===0;});
  el.innerHTML=fl.length?
    fl.map(function(a){
      return '<button data-a="qa:'+esc(a)+'">'+esc(a)+'</button>';}).join(""):
    '<span class="hint">no form_* roster for target</span>';
}

// ---- order console ----
function conAct(a){
  var pt=clickPts.length?clickPts[clickPts.length-1]:null;
  // battlefield probes: no unit target needed
  if(a==="vp") return fire("vp");
  if(a==="ships") return fire("ships");
  if(a==="elev"){
    if(!pt){ toast("click the map to set the sample point first"); return; }
    return fire("elev "+fx(pt[0])+" "+fx(pt[1]));
  }
  if(a==="bldprobe"){                      // each click: next chunk of the registry
    var bm2=(document.getElementById("bmode")||{value:"full"}).value;
    return fire("bld "+Math.max(1,Math.round(num("bchunk",250)))+
      (bm2!=="full"?" "+bm2:""));
  }
  if(a==="bldbisect"){                     // freeze bisect: one engine call per click
    var bs=(document.getElementById("bstep")||{value:""}).value.trim();
    return fire("bldstep"+(bs?" "+Math.max(1,Math.round(+bs||1)):""));
  }
  if(a==="aeq") return fire("aeq");
  var k=targetKey;
  if(!k){ toast("left-click an AI unit to target it first"); return; }
  var run=runMode?1:0, line=null;
  function needPt(){ if(!pt){toast("left-click the map to set a point first");return false;}
    return true; }
  if(a==="occupy"){ if(!needPt())return; line="occupy "+k+" "+fx(pt[0])+" "+fx(pt[1])+" "+run; }
  else if(a==="halt") line="halt "+k;
  else if(a==="withdraw") line="withdraw "+k+" "+run;
  else if(a==="rotm") line="rotate "+k+" -45";
  else if(a==="rotp") line="rotate "+k+" 45";
  else if(a==="stepf") line="stepf "+k;
  else if(a==="stepb") line="stepb "+k;
  else if(a==="faw1") line="faw "+k+" 1";
  else if(a==="faw0") line="faw "+k+" 0";
  else if(a==="mlee1") line="mlee "+k+" 1";
  else if(a==="mlee0") line="mlee "+k+" 0";
  else if(a==="shot"){ var sn=val("shot");
    if(!sn){toast("enter a shot-type name");return;} line="shot "+k+" "+sn; }
  else if(a==="beh1"||a==="beh0") line="beh "+k+" "+val("behsel")+" "+(a==="beh1"?1:0);
  else if(a==="winc") line="winc "+k;
  else if(a==="wdec") line="wdec "+k;
  else if(a==="abil"){ var an=val("abil").split(/\s+/)[0];
    if(!an){toast("enter an ability name");return;} line="abil "+k+" "+an; }
  else if(a==="take"||a==="release"||a==="leavebld")
    line=a+" "+k;
  else if(a==="battk"||a==="climb"||a==="defendbld")
    line=a+" "+k+" "+Math.round(num("sbi",1));
  else if(a==="usedep"||a==="usedep2")
    line=a+" "+k+" "+Math.round(num("sdi",1));
  else if(a.indexOf("tg:")===0){ var q=a.split(":"); line=q[1]+" "+k+" "+q[2]; }
  else if(a.indexOf("qa:")===0) line="abil "+k+" "+a.slice(3);
  if(line) fire(line);
}
var notesTimer=null;
function toggleNotes(){
  var b=document.getElementById("notesbox");
  b.className=b.className==="open"?"":"open";
  if(b.className==="open") document.getElementById("fnotes").focus();
}
function initNotes(){
  fetch("/notes").then(function(r){return r.json();}).then(function(d){
    document.getElementById("fnotes").value=(d&&d.text)||"";
  }).catch(function(){});
  document.getElementById("fnotes").addEventListener("input",function(){
    document.getElementById("nstat").textContent="...";
    clearTimeout(notesTimer);
    notesTimer=setTimeout(function(){
      post("/notes",{text:document.getElementById("fnotes").value},function(d){
        document.getElementById("nstat").textContent=
          (d&&d.ok)?"saved":"SAVE FAILED";
      });
    },700);
  });
}
var frozen=true;   // harness.lua default: freeze ON while armed
function toggleFreeze(){
  frozen=!frozen;
  fire("freeze all "+(frozen?"1":"0"));
  var b=document.getElementById("frz");
  b.className=frozen?"on":"";
  b.textContent=frozen?"AI: FROZEN":"AI: FREE";
}
function fire(line){
  toast("→ "+line);
  post("/test_order",{line:line},function(d){
    if(d&&d.ok) orders.unshift({seq:d.seq,line:line,status:"pending",t:Date.now()});
    else orders.unshift({seq:null,line:line,status:"err",
      err:(d&&d.error)||"send failed",t:Date.now(),done:Date.now()});
    if(orders.length>8) orders.length=8;
    renderOrders();
  });
}
// status strip, not a log: only the newest order shows (+ a queued count),
// clearing itself ~6 s after the ack lands (errors linger 15 s). Orders
// queue server-side, one relayed per ack — orders given while the game is
// paused burst-execute in sequence on unpause, like the real game.
function renderOrders(){
  var el=document.getElementById("olog"); if(!el) return;
  var o=orders[0];
  var linger=(o&&o.status==="err")?15000:6000;
  if(!o||(o.status!=="pending"&&o.done&&Date.now()-o.done>linger)){
    el.innerHTML=""; return; }
  var cls=o.status==="ok"?"ok":(o.status==="pending"?"pend":
    (o.status==="skip"?"skip":"err"));
  var lab=o.status==="ok"?("OK"+(o.val!=null?" = "+o.val:"")):
    (o.status==="pending"?"...":(o.err||o.status));
  var np=orders.filter(function(x){return x.status==="pending";}).length;
  el.innerHTML='<div class="oline"><code>'+esc((o.seq!=null?"#"+o.seq+" ":"")+o.line)+
    '</code><span class="chip '+cls+'">'+esc(lab)+'</span>'+
    (np>1?'<span class="hint">+'+(np-1)+' in queue</span>':'')+'</div>';
}
setInterval(function(){                       // ack watcher + status-strip fade
  if(!orders.some(function(o){return o.status==="pending";})){ renderOrders(); return; }
  fetch("/test_ack",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(a){
      var now=Date.now();
      orders.forEach(function(o){
        if(o.status!=="pending") return;
        if(a&&a.seq===o.seq){ o.status=a.ok?"ok":"err"; o.err=a.err||null;
          o.val=(a.val!=null)?String(a.val):null; o.done=now; }
        else if(a&&typeof a.seq==="number"&&a.seq>o.seq){
          o.status="skip"; o.err="done (ack overtaken)"; o.done=now; }
        else if(now-o.t>30000){ o.status="err";
          o.err="no ack in 30s \u2014 harness off / no battle / paused?"; o.done=now; }
      });
      renderOrders();
    }).catch(function(){});
},700);

// ---- item rows + verdicts ----
function renderItems(){
  var c=curSub(), box=document.getElementById("items");
  if(!c){box.innerHTML="";return;}
  var h="";
  if(!c.sub.items.length)
    h='<div class="hint" style="padding:8px 2px">no T1/T2 items in this subsection '+
      '(native-tier work is out of harness scope)</div>';
  c.sub.items.forEach(function(it){
    var how=howTo(it);
    h+='<div class="item" data-id="'+esc(it.id)+'">'+
      '<div class="itop"><span class="iname">'+esc(it.name)+'</span>'+
      '<span class="chip t'+it.tier+'">T'+it.tier+'</span>'+
      '<span class="chip '+(it.status==="YES"?"yes":"nyet")+'">'+esc(it.status)+'</span></div>'+
      '<div class="mth"><code>'+esc(it.method)+'</code></div>'+
      (how?'<div class="hint" style="margin:2px 0 4px">&#9656; '+esc(how)+'</div>':'')+
      '<div class="ictl">'+
      '<button class="vb pass'+(it.verdict==="pass"?" on":"")+'" data-v="pass">PASS</button>'+
      '<button class="vb fail'+(it.verdict==="fail"?" on":"")+'" data-v="fail">FAIL</button>'+
      '<input class="note" placeholder="note" value="'+
      esc(it.note||noteDraft[it.id]||"")+'"></div></div>';
  });
  box.innerHTML=h;
  box.querySelectorAll(".item").forEach(function(row){
    var id=row.dataset.id, it=findItem(id); if(!it) return;
    row.querySelectorAll(".vb").forEach(function(b){
      b.onclick=function(){
        var nv=(it.verdict===b.dataset.v)?null:b.dataset.v;
        var note=row.querySelector(".note").value;
        it.verdict=nv; it.note=note; noteDraft[id]=note;
        post("/verdict",{id:id,verdict:nv,note:note});
        renderNav(); renderItems();
      };
    });
    // notes save on a debounce, verdict or not -- typed text must survive
    // re-renders (draft synced per keystroke) and page reloads (server keeps
    // note-only entries)
    var ninp=row.querySelector(".note"), nT=null;
    ninp.oninput=function(){
      var note=ninp.value;
      noteDraft[id]=note; it.note=note;
      clearTimeout(nT);
      nT=setTimeout(function(){
        post("/verdict",{id:id,verdict:it.verdict||null,note:note});
      },600);
    };
  });
}

// ---- per-item test instructions ----
// EVERY item gets a line: either the exact lever to pull, or the exact reason
// it cannot be pulled yet. Rule order matters (substring ids overlap).
function howTo(it){
  var id=it.id;
  function has(p){return id.indexOf(p)>=0;}
  // reads: units
  if(has("unit-type-key")||has("unit-name")||has("unit-role-class")) return "select any unit - type/name/class fill the detail panel";
  if(has("archetype")) return "select different unit kinds - infantry/cavalry/artillery badges flip";
  if(has("position-xyz")) return "select a unit - the pos row updates live as it moves";
  if(has("orientation-bearing")) return "select a unit - the gold map arrow is its facing";
  if(has("running-walking-idle")) return "move a unit in game - moving/idle/running badges track it";
  if(has("frontage-ordered")) return "drag-widen a unit's line in game - ordered width row changes";
  if(has("frontage-current")) return "no read path (T3) - nothing to test here";
  if(has("officer-position")) return "select a unit - the white map dot is its officer";
  if(has("distance-to-another")||has("in-range-check")) return "distance tool below: pick a second unit - violet line + range check";
  if(has("number-of-soldiers")) return "watch the men bar in a fight - it tracks losses live";
  if(has("kills-made")) return "select a fighting unit - the kills row climbs";
  if(has("resources-ammo")) return "let archers shoot - ammo bar drains";
  if(has("missile-range-live")) return "select a ranged unit - range row; melee units read 'melee'";
  if(has("-routing")||has("-shattered")) return "watch a breaking unit - badges + flashing map marker";
  if(has("under-missile-fire")) return "select a unit under arrow fire - under-fire badge";
  if(has("resources-hidden")||has("valid-target")||has("visible-to")) return "hide a unit in forest - hidden/visible/valid-target badges flip";
  if(has("withdrawing")) return "CANDIDATE FOUND 07-30: the 'Unit Left Battlefield' command event, captured engine-side (aai_cmds.json) - the detector pairs it with whichever unit vanishes from the feed; no on-page log per the log-free rule";
  if(has("garrisoned")) return "engine 'garrisoned' = INSIDE an occupiable building, not on a wall (wall-standing correctly reads false) - siege test: DEFEND a unit into a building, badge + bld-probe garr column should both flip";
  if(has("naval-fire-at-will")) return "fire_at_will's ship twin - needs a NAVAL battle: toggle fire-at-will on a ship unit, the badge is the read under test";
  if(has("fire-at-will-mode")) return "toggle fire-at-will in the game UI - badge tracks it (the one UI toggle that reflects)";
  if(has("loose-spacing")) return "toggle loose formation in the game UI - the badge tracks it (with fire-at-will, one of the two UI toggles that reflect); engine behaviour name: change_formation_spacing";
  if(has("skirmish-mode")) return "proven read - UI skirmish stance on a skirmisher-class unit (or stances-panel on/off): the badge tracks it";
  if(has("defend-mode")) return "the hold-ground toggle - stances panel on/off on melee infantry: badge + the unit standing its ground instead of chasing = pass";
  if(has("formed-attack-mode")) return "stances panel on/off on formation infantry: badge + ranks held while attacking = pass";
  if(has("dismount-mode")) return "stances panel on/off on CAVALRY: badge + riders dismounting = pass";
  if(has("release-animals-mode")) return "stances panel on/off on a war-dog/beast unit: badge + handlers releasing the animals = pass";
  if(has("drop-siege-equipment-mode")) return "siege attacker carrying a ram/ladder/tower - stances panel on/off: badge + the equipment dropped = pass";
  if(has("abandon-artillery-engines-mode")) return "stances panel on/off on artillery crew: badge + the crew walking off the engines = pass";
  if(has("board-ship-mode")) return "naval/amphibious battle - stances panel on/off: badge + boarding motion = pass";
  if(has("active-formation")) return "FAIL stands (07-30): the isolated retest closed the event route - 'Special Ability' casts carry no unit (get_unit is nil on them, works on other events); the badges stay dark until a T3 native read is built";
  if(has("unlimber-mode")) return "artillery only - artillery LIMBERS to move and unlimbers to deploy: give arty a move order and watch the badge flip during the trek, then let it stop and redeploy";
  if(has("ordered-destination")) return "give a move order - cyan dashed line to the ordered point";
  if(has("order-type")) return "PASS stands (07-29) - order names are captured engine-side (aai_cmds.json); no on-page log per the log-free rule";
  if(has("formations-roster")) return "WHAT: the list of formations this unit type can take (Shield Wall, Testudo...). WHERE: the formations badges on the stances page (hover one for its effect). TEST: compare the badge list vs the unit's in-game formation buttons - an accurate list = pass (the lit/unlit state is the separate active-formation line)";
  if(has("display-names")) return "WHAT: the translation from engine keys to the labels the game shows (form_hoplite_phalanx -> 'Spear Wall') - every badge/row on these pages is labeled through it. TEST: if the names here match what the game UI calls them, pass";
  if(has("unit-abilities-roster")) return "WHAT: the list of special abilities this unit type owns (e.g. Precision Shot on archers). WHERE: the unit-abilities badges + FIRE rows on the abilities page. TEST: select units and compare the badge list vs that unit's ability buttons in the game UI - an accurate list = pass";
  if(has("general-abilities-active")) return "WHAT: which rally the general is casting RIGHT NOW - the read that lights the badges on the GENERAL page. TEST: click the general (army slot 1), cast rally (UI on yours / FIRE on the AI's) and watch the badge; caveat: this read was DEAD for UI casts of unit abilities, so the script-cast is the decisive test";
  if(has("general-abilities-roster")) return "WHAT: the general tier itself - rally x5 star-levels (DB: unit_abilities.requires_effect_enabling + the effect junction), not per-unit-type. WHERE: the GENERAL page - click the general's unit (army slot 1, either side); hover a badge for its effect. TEST: the badge list matching the game's general abilities = pass";
  if(has("current-ability")) return "WHAT: which ability the unit is casting RIGHT NOW - the read that lights the ability badges green. TEST: FIRE a roster ability on an AI unit and watch the badge + the current row; a visible in-game effect while everything stays dark = the read failing, not the cast";
  if(has("can-perform")) return "WHAT: whether the unit could cast an ability at this moment (eligibility/cooldown) - the 'has ability'/'can perform' badges at the top of the abilities panel. TEST: watch them against an ability you know is ready vs one just spent";
  if(has("ability-parameters")) return "WHAT: an ability's DB numbers (duration, recharge, targeting...). WHERE: the small line under each FIRE row on the abilities panel. TEST: spot-check a few vs the in-game tooltips";
  if(has("ability-effects")) return "WHAT: the buffs/debuffs an ability applies. WHERE: hover any ability badge (formations, unit abilities, general abilities) - the tooltip is the game's own description; phase stat-effects also render under the FIRE rows. TEST: compare vs the in-game tooltip";
  if(has("global-combat-rules")) return "extracted (kv: 472 constants) - open /abilitystats in a browser tab, 'kv' block; spot-check a few values";
  if(has("experience-curves")) return "extracted - /abilitystats 'experience' block (18 thresholds + 10 bonus levels); compare vs a ranked unit";
  if(has("naval-card")) return "hover a ship unit in a NAVAL battle - the naval group at the card bottom (can ram/board, hull hp)";
  if(has("first-strike")||has("vs-missiles")||has("mount-entity")||has("explosion")) return "not in the extract yet (schema/table pending) - nothing to verify";
  if(has("unit-card")) return it.status==="YES"?
    "CLICK a unit (map or list) - the full stat sheet pops up, scrollable (same fields for every unit, dash = doesn't apply) - compare 'as the game card shows it' vs the in-game unit card":
    "CLICK a unit (map or list) - the full stat sheet pops up, scrollable (dash = doesn't apply) - compare the extracted detail groups vs the game UI and verdict";
  // reads: battlefield / flow (specific before generic)
  if(has("victory-points")) return "victory-points panel: the capture-events readout updates when a capture starts/completes (needs a fort/siege battle); live region STATE is T5, out of scope";
  if(has("weather-read")) return "no reader exists (writers only) - T3 territory, out of harness scope";
  if(has("battle-timer")||has("time-remaining")||has("battle-over")) return "battle-flow panel: 'engine time remaining' is the authoritative clock (tick clock drifts under speed-ups); battle-over row flips at the end";
  if(has("battle-phase")||has("player-side")||has("commander")) return "top bar + this panel show the live values";
  if(has("camera")) return "this panel shows camera pos/target live - move your view";
  // writes: specific levers first
  if(has("set-ammo-type")) return "FAIL 07-29: the engine rejects card names ('does not support this shot type') - the valid values are DB shot-type keys; surfacing each unit's own key list is the next attacking-category fix";
  if(has("cast-general")) return "GENERAL page: select the AI general's unit (army slot 1) - FIRE an att_gen_rally_0N (star level must match his command stars); a visible rally aura in game is the verdict (rally on any other unit errors 'does not support')";
  if(has("set-formation")) return "stances WRITE console form_* box, or the form_* fire buttons on the stances read panel - Theo 07-29: some fire visibly, some silently no-op; what separates them is the open question";
  if(has("use-ability")) return "ABILITIES panel FIRE buttons (unit-ability tier - formations live on the stances page) - the verdict is whether the EFFECT visibly happens in game (Theo 07-29: some do, some don't)";
  if(has("bind-units")) return "what it is: one unit_controller driving SEVERAL units at once - the script's version of a drag-selected group (batch orders). Already exercised live: the freeze system binds every AI unit each pump";
  if(has("bind-group")) return "what it is: bind a pre-made army GROUP to a controller in one call (vs bind-units one by one). NO LEVER YET - needs 2+ bound-unit plumbing; deferred";
  if(has("enable-disable")) return "control panel: enable/disable on/off - what change_enabled visibly does to the unit is exactly the open question";
  if(has("deploy-reinforcement")) return "WRITE console > control: reinforcement deploy on/off - the call acks now; the true effect needs a battle with reinforcements due";
  if(has("equip-item")) return "DEAD END so far: no mechanism found in any layer - nothing to test";
  if(has("mount-climb")||has("dismount")||has("defend-building")||has("use-siege")) return "WRITE > walls console: run BLD PROBE / AEQ PROBE first (READ > Buildings), then CLIMB/LEAVE/DEFEND/OCCUPY by index - siege battle";
  if(has("attack-building")) return "WRITE > walls console: ATTACK BLDG by row index (run SHOW BUILDINGS first) - siege battle; (the scripted-destroy button was removed from the buildings panel at Theo's request 07-31 - the verb still exists in the harness)";
  if(has("disembark")||(has("naval")&&id.indexOf("write-")===0)) return "needs a NAVAL battle (ram/board are T4 - no Lua verb)";
  // reads that need special battles (after the specific write rules)
  if(has("reinforcement")) return "battle-flow panel: per-army reinforcement + reinf-ship counts are live rows (a battle with reinforcements due makes them nonzero)";
  if(has("naval")||has("ships-list")) return "battle-flow panel: ships row per army is live (0 on land - a NAVAL battle lights it)";
  if(has("buildings")||has("siege")) return "buildings panel: VERDICT 07-31 - the Lua scan route is CLOSED (cold registry slots are undetectable: cold metatable = warm metatable; even a names-only touch kills the feed; warmth is a live streaming state, so no timing rule helps). Buildings continue on the native/static route: offline map geometry (SIEGE_GEOMETRY.md, solved) + map identity via the native DLL. The scan buttons remain for deliberate throwaway experiments; AEQ PROBE = assault equipment";
  if(has("elevation")) return "terrain panel: click the map, ELEV SAMPLE - the ground height comes back as OK = <value> on the ack line";
  if(id.indexOf("write-")===0) return "order console above: frozen AI target, fire the verb, watch the field + ack chip";
  return "no dedicated lever wired - method: "+it.method+" (tell Claude in notes if you want one built)";
}

// ---- top bar + polling ----
function updateTop(){
  var f=document.getElementById("feed"), t=document.getElementById("tgtlab"),
      w=document.getElementById("warn");
  if(!state||feedAge==null)
    f.innerHTML='<span class="bad">NO FEED</span>';
  else
    f.innerHTML='phase <b>'+esc(state.phase||"?")+'</b> &middot; t <b>'+
      (state.t!=null?Math.round(state.t):"?")+'s</b>'+
      (state.remaining!=null?' &middot; rem <b>'+Math.round(state.remaining)+'s</b>':'')+
      ' &middot; age '+feedAge+'s'+
      (feedAge>3?' <span class="bad">FEED STALE</span>':"");
  t.innerHTML='target <b>'+(targetKey?esc(targetKey):"\u2014")+'</b>'+
    (runMode?' \u00b7 <b style="color:var(--gold)">RUN</b>':'');
  w.style.display=(state&&state.phase==="conflict")?"none":"inline-block";
}
var cmds=null, bldData=null, capevData=null;
function poll(){
  fetch("/state",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(d){
      state=d.battle||null;
      feedAge=(d.battle_age==null?null:d.battle_age);
      updateTop(); renderDetail(); renderTsel(); renderUlist();
    }).catch(function(){ feedAge=null; updateTop(); });
  if(mapKind==="r_orders"||mapKind==="r_abil"||mapKind==="r_stance")
    fetch("/cmds",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(d){ cmds=d; }).catch(function(){});
  if(mapKind==="r_bld")
    fetch("/bld",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(d){ bldData=d; }).catch(function(){});
  if(mapKind==="r_vp")
    fetch("/capev",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(d){ capevData=d; }).catch(function(){});
}
// display name for an ability key: the game's own localisation (the
// abilitystats "names" map) with a readable fallback while it loads
// hover text for an ability badge: the game's own tooltip body (buffs/
// debuffs in the game's words), else the DB params/effects summary
function abTip(k){
  var t=abinfo&&abinfo.tips&&abinfo.tips[k];
  if(t) return t;
  var inf=abinfo&&abinfo.abilities&&abinfo.abilities[k];
  return inf?abilSummary(inf):null;
}
function abName(k){
  var n=abinfo&&abinfo.names&&abinfo.names[k];
  return n||String(k).replace(/^form_/,"").replace(/_/g," ");
}
// both-sides ability board (abilities page, nothing selected): every unit's
// DB roster + live cast state, player and AI
function abilBoardHtml(){
  var h='';
  [["player","#5aa2ff"],["ai","#ff5b5b"]].forEach(function(sd){
    var us=allUnits().filter(function(o){return o.side===sd[0];});
    if(!us.length) return;
    h+='<div class="shdr" style="color:'+sd[1]+'">'+sd[0]+' - unit abilities</div>';
    us.forEach(function(o){
      var u=o.u, s=stats[u.type], ros=((s&&s.abilities)||[]).filter(function(a){return a.indexOf("form_")!==0;});
      var live=u.ability?('<b style="color:var(--gold)">casting '+esc(abName(u.ability))+'</b>')
        :(u.can_ability?'ready':(ros.length?'':'—'));
      h+='<div class="row"><span>'+esc(u.name||"?")+'</span><span>'+live+'</span></div>';
      if(ros.length)
        h+='<div class="hint" style="margin:0 0 3px 10px">'+esc(ros.map(abName).join(" · "))+'</div>';
    });
  });
  h+='<div class="hint" style="margin-top:4px">reads work for BOTH sides - only '+
    'FIRE (script-cast) needs an AI unit; select any unit for its full panel</div>';
  return h;
}
// the general (rally) tier — fixed 5 star-levels, not unit-rostered
var GEN_ABILS=["att_gen_rally_01","att_gen_rally_02","att_gen_rally_03",
               "att_gen_rally_04","att_gen_rally_05"];
// both-sides general board (general-abilities page, nothing selected):
// each army's slot-1 unit (= the general) + the rally badges
function genBoardHtml(){
  var h='';
  [["player","#5aa2ff"],["ai","#ff5b5b"]].forEach(function(sd){
    var us=allUnits().filter(function(o){return o.side===sd[0]&&o.u.i===1;});
    if(!us.length) return;
    h+='<div class="shdr" style="color:'+sd[1]+'">'+sd[0]+' - general</div>';
    us.forEach(function(o){
      var u=o.u;
      h+='<div class="row"><span>'+esc(u.name||"?")+' ('+esc(u.type||"")+
        ')</span><span></span></div><div style="margin:0 0 3px 10px">';
      GEN_ABILS.forEach(function(a){ h+=badge(abName(a),u.ability===a,abTip(a)); });
      h+='</div>';
    });
  });
  h+='<div class="hint" style="margin-top:4px">army slot 1 = the general&#39;s '+
    'unit; click it for the full panel (FIRE on the AI side) - hover a badge '+
    'for its effect</div>';
  return h;
}
// both-sides formation board (stances page, nothing selected): every unit's
// form_* roster + live cast state, player and AI
function formBoardHtml(){
  var h='';
  [["player","#5aa2ff"],["ai","#ff5b5b"]].forEach(function(sd){
    var us=allUnits().filter(function(o){return o.side===sd[0];});
    if(!us.length) return;
    var any=false;
    var hh='<div class="shdr" style="color:'+sd[1]+'">'+sd[0]+' - formations by unit</div>';
    us.forEach(function(o){
      var u=o.u, s=stats[u.type];
      var forms=((s&&s.abilities)||[]).filter(function(a){return a.indexOf("form_")===0;});
      if(!forms.length) return;
      any=true;
      var live=(u.ability&&u.ability.indexOf("form_")===0)?
        '<b style="color:var(--gold)">'+esc(abName(u.ability))+'</b>':'';
      hh+='<div class="row"><span>'+esc(u.name||"?")+'</span><span>'+live+'</span></div>'+
        '<div class="hint" style="margin:0 0 3px 10px">'+esc(forms.map(abName).join(" · "))+'</div>';
    });
    if(any) h+=hh;
  });
  h+='<div class="hint" style="margin-top:4px">the unit-formations tier (testudo, '+
    'shield wall...) - each unit&#39;s own roster; the gold highlight needs the '+
    'native active-formation read (not built)</div>';
  return h;
}
function loadCaps(){
  fetch("/capabilities",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(d){
      caps=d;
      if(!active&&caps.sections.length) setActive(0,0);
      else { renderNav(); renderSeg(); }
    }).catch(function(){});
}
function loadStats(){
  fetch("/unitstats",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(d){ if(d&&typeof d==="object") stats=d; renderAbilQuick(); })
    .catch(function(){});
}
var abinfo=null;
function loadAbil(){
  fetch("/abilitystats",{cache:"no-store"}).then(function(r){return r.json();})
    .then(function(d){ if(d&&typeof d==="object") abinfo=d; })
    .catch(function(){});
}

loadCaps(); loadStats(); loadAbil(); updateTop(); initNotes();
setInterval(poll,1000); poll(); drawMap();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, body, ctype="application/json", code=200):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path
        if p == "/":
            self._send(PAGE, "text/html; charset=utf-8")
        elif p == "/harness":
            self._send(PAGE_HARNESS, "text/html; charset=utf-8")
        elif p == "/capabilities":
            self._send(json.dumps(capabilities_payload()))
        elif p == "/test_ack":
            pump_orders()
            ack, _age = read_json(TEST_ACK_FILE)
            self._send(json.dumps(ack if ack else {}))
        elif p == "/notes":
            self._send(json.dumps({"text": load_notes()}))
        elif p == "/cmds":
            cm, _age = read_json(CMDS_FILE)
            cm = cm if isinstance(cm, dict) else {}
            now = time.time()
            with _cast_lock:
                cm["castacc"] = {k: {"c": v["c"], "ago": int(now - v["t"])}
                                 for k, v in _cast_acc.items()}
            self._send(json.dumps(cm))
        elif p == "/state":
            pump_orders()
            battle, age = read_json(BATTLE_FILE)
            self._send(json.dumps({"battle": battle, "battle_age": age}))
        elif p == "/geometry":
            geo, age = read_json(GEOMETRY_FILE)
            self._send(json.dumps(geo if geo else {}))
        elif p == "/unitstats":
            st, age = read_json(STATS_FILE)
            self._send(json.dumps(st if st else {}))
        elif p == "/abilitystats":
            ab, _age = read_json(ABILSTATS_FILE)
            self._send(json.dumps(ab if ab else {}))
        elif p == "/bld":
            bl, _age = read_json(BLD_FILE)
            self._send(json.dumps(bl if bl else {}))
        elif p == "/capev":
            cv, _age = read_json(CAPEV_FILE)
            self._send(json.dumps(cv if cv else {}))
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        if self.path not in ("/verdict", "/test_order", "/notes"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n)) if n else {}
            if self.path == "/verdict":
                save_verdict(req)
                self._send(json.dumps({"ok": True}))
            elif self.path == "/notes":
                save_notes(req.get("text", ""))
                self._send(json.dumps({"ok": True}))
            else:  # /test_order
                seq, queued = send_test_order(req)
                self._send(json.dumps({"ok": True, "seq": seq, "queued": queued}))
        except Exception as e:
            self._send(json.dumps({"ok": False, "error": str(e)}), code=400)

    def log_message(self, *a):
        pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False   # Windows: never bind over a live listener

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        super().server_bind()

    def handle_error(self, request, client_address):
        import sys
        log("request error from %s: %r" % (client_address, sys.exc_info()[1]))


def main():
    server = None
    for family, addr in ((socket.AF_INET6, ("::", PORT)), (socket.AF_INET, ("127.0.0.1", PORT))):
        QuietServer.address_family = family
        try:
            server = QuietServer(addr, Handler)
            break
        except OSError:
            server = None
    if server is None:
        return
    stack = "dual-stack" if server.address_family == socket.AF_INET6 else "ipv4-only"
    log("battle cockpit up: http://localhost:%d  game=%s  (%s)" % (PORT, GAME, stack))
    server.serve_forever()


if __name__ == "__main__":
    main()
