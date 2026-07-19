"""Attila-AI BATTLE cockpit -- view + command the battle AI.

Battle-only. Serves http://localhost:8199/ : a canvas top-down map of every
unit + all buildings, a hover panel (live reads + static unit-DB stats) with a
range ring, a real Total-War command bar (context-filtered per unit; cheats in
a separate tray), red attack pathfinding, multi-select, and a REPLAY mode.

Controls: left-click AI unit = select · shift-click = add/remove · shift-drag =
box-select · left-drag = pan · right-click = move (or attack the enemy under the
cursor; alt+right = attack ground) · right-drag = face + frontage · wheel = zoom
· R = run toggle.

Reads the mod's exporter (src/battle/publish.lua):
  data/aai_battle.json           live per-tick state (all units, camera)
  data/aai_battle_<id>.jsonl     per-battle recording (geometry + state + cmds)
  data/aai_unit_stats.json       static per-unit combat stats (built offline)
Writes orders to data/aai_orders.txt (consumed by src/battle/ai_link.lua;
control requires the flag file data/aai_battle_ai_on.txt).

Spawned at boot by src/frontend/spawn_viz.lua; duplicate instances exit because
the port is bound. Run by hand: python viz/aai_viz.py
"""

import glob
import json
import os
import re
import socket
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


# ----------------------------------------------------------------- orders

def _put(key, channel, line):
    _pending[(key, channel)] = (line, time.time() + ORDER_TTL)


def queue_battle_order(req):
    """One command per POST -> the appropriate slot, then rewrite the orders file.
    Commands mirror ai_link's verbs: move/form/apos/aunit/stop/flee/fire/mlee/
    spd/act/abil/release."""
    verb = req.get("verb")
    key = req.get("key")
    if not verb or not key:
        return
    n = lambda k, d=0: req.get(k, d)
    if verb == "release":
        for k in [k for k in _pending if k[0] == key]:
            del _pending[k]
        _put(key, "rel", "release %s" % key)
    elif verb in ("move", "apos"):
        _pending.pop((key, "rel"), None)
        run = " run" if req.get("run") else ""
        _put(key, "move", "%s %s %g %g%s" % (verb, key, n("x"), n("z"), run))
    elif verb == "aunit":
        _pending.pop((key, "rel"), None)
        _put(key, "move", "aunit %s %g %g" % (key, n("x"), n("z")))
    elif verb == "form":
        _pending.pop((key, "rel"), None)
        run = " run" if req.get("run") else ""
        _put(key, "move", "form %s %g %g %g %g%s"
             % (key, n("x"), n("z"), n("bearing"), n("width"), run))
    elif verb == "stop":
        _pending.pop((key, "rel"), None)
        _put(key, "move", "stop %s" % key)
    elif verb == "flee":
        _pending.pop((key, "rel"), None)
        run = " run" if req.get("run") else ""
        _put(key, "move", "flee %s%s" % (key, run))
    elif verb == "fire":
        _put(key, "fire", "fire %s %d" % (key, 1 if req.get("on") else 0))
    elif verb == "mlee":
        _put(key, "mlee", "mlee %s %d" % (key, 1 if req.get("on") else 0))
    elif verb == "spd":
        _put(key, "spd", "spd %s %g" % (key, n("mult", 1)))
    elif verb == "act":
        _put(key, "act", "act %s %d %s" % (key, int(n("id", 1)), req.get("act", "default")))
    elif verb == "abil":
        # seq = monotonic dedup counter; idx = which ability (1 or 2)
        _put(key, "abil", "abil %s %d %d" % (key, int(n("seq", 1)), int(n("idx", 1))))
    else:
        return
    _write_orders()


def _write_orders():
    now = time.time()
    lines = ["seq %d" % next_seq()]
    for k in list(_pending):
        line, exp = _pending[k]
        if exp < now:
            del _pending[k]
            continue
        lines.append(line)
    tmp = ORDERS_OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ORDERS_OUT)


# ----------------------------------------------------------------- replays

def list_replays():
    out = []
    for p in glob.glob(os.path.join(DATA, "aai_battle_*.jsonl")):
        try:
            out.append({"file": os.path.basename(p),
                        "size": os.path.getsize(p),
                        "mtime": os.path.getmtime(p)})
        except OSError:
            pass
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def read_replay(name):
    if not re.match(r"^aai_battle_[\w.-]+\.jsonl$", name or ""):
        return None
    path = os.path.join(DATA, name)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


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
  <div class="seg"><button id="mLive" class="on">LIVE</button><button id="mReplay">REPLAY</button></div>
  <span class="meta" id="status"></span>
  <div id="rep">
    <select id="repSel"></select>
    <button class="tw" id="repPlay" style="min-width:52px">Play</button>
    <input type="range" id="tl" min="0" max="0" value="0">
    <span class="meta" id="repT"></span>
  </div>
</div>
<div id="main">
  <canvas id="c"></canvas>
  <div id="side"></div>
</div>
<div id="bar">
  <div id="barhead"></div>
  <div id="btns"></div>
</div>
<script>
var cv=document.getElementById("c"), ctx=cv.getContext("2d");
var geom=null, state=null, live=true, stats={};
var view={zoom:1,panx:0,pany:0}, fitC=null;
var sel={}, hover=null;            // sel: set of selected AI keys
var lastKind={}, meleeOn={}, runMode=false, showCheats=false;
var actSeq=1, abilSeq=1;
var drag=null;                     // active mouse gesture
var rep={frames:[],cmds:[],geom:null,i:0,playing:false,file:null};
var battleAge=0;

function resize(){cv.width=cv.clientWidth; cv.height=cv.clientHeight;}
window.addEventListener("resize",resize); resize();

// ---- data source ----
function curState(){ return live?state:(rep.frames[rep.i]||null); }
function curGeom(){ return live?geom:(rep.geom||geom); }

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
function selKeys(){ return Object.keys(sel); }
function selUnits(st){ var out=[]; selKeys().forEach(function(k){
    var o=findUnit(st,k); if(o&&o.key) out.push(o); }); return out; }
function selCount(){ return selKeys().length; }

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
  ctx.rotate(-br);           // screen y is down; bearing clockwise-from-north
  var hw=f.hw*S, hd=f.hd*S;
  ctx.fillStyle=col; ctx.globalAlpha=(u.vis===false&&o.side==="ai")?.4:.92;
  ctx.fillRect(-hw,-hd,2*hw,2*hd);
  ctx.globalAlpha=1;
  ctx.strokeStyle="rgba(255,255,255,.5)"; ctx.beginPath();
  ctx.moveTo(0,0); ctx.lineTo(0,-hd-4); ctx.stroke();
  if(sel[o.key]){ ctx.lineWidth=2; ctx.strokeStyle="#fff";
    ctx.strokeRect(-hw-2,-hd-2,2*hw+4,2*hd+4); ctx.lineWidth=1; }
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
  var atk=lastKind[o.key]==="attack";
  ctx.strokeStyle= atk?"#ff3b3b":"#7dd3fc"; ctx.globalAlpha=.85; ctx.lineWidth=1.5;
  ctx.setLineDash(atk?[]:[5,4]); ctx.beginPath();
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
function drawGesture(){
  if(!drag||!drag.moved) return;
  if(drag.btn===0&&drag.shift){                 // box select
    ctx.strokeStyle="rgba(217,180,91,.9)"; ctx.setLineDash([4,3]); ctx.lineWidth=1;
    ctx.strokeRect(Math.min(drag.sx0,drag.cx),Math.min(drag.sy0,drag.cy),
      Math.abs(drag.cx-drag.sx0),Math.abs(drag.cy-drag.sy0)); ctx.setLineDash([]);
  } else if(drag.btn===2){                       // orientation: preview the arrangement
    var us=selUnits(curState()), N=us.length; if(!N) return;
    var pl=orientPlan([drag.wx0,drag.wz0], s2w(drag.cx,drag.cy), N); if(!pl) return;
    var S=fitC.s*view.zoom, brad=pl.bearing*Math.PI/180;
    var a=w2s(drag.wx0,drag.wz0), b=w2s(s2w(drag.cx,drag.cy)[0],s2w(drag.cx,drag.cy)[1]);
    ctx.strokeStyle="rgba(217,180,91,.85)"; ctx.lineWidth=1.5; ctx.setLineDash([5,3]);
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); ctx.setLineDash([]);
    for(var i=0;i<N;i++){                          // ghost box per unit, at its slot + facing
      var c=w2s(pl.pts[i][0],pl.pts[i][1]);
      var f=unitFoot(us[i].u), hw=Math.max(4,(pl.each/2)*S), hd=Math.max(3,f.hd*S);
      ctx.save(); ctx.translate(c[0],c[1]); ctx.rotate(-brad);
      ctx.strokeStyle="rgba(217,180,91,.95)"; ctx.lineWidth=1.5;
      ctx.strokeRect(-hw,-hd,2*hw,2*hd);
      ctx.strokeStyle="rgba(255,255,255,.7)"; ctx.beginPath();
      ctx.moveTo(0,0); ctx.lineTo(0,-hd-5); ctx.stroke();       // facing tick
      ctx.restore();
    }
    var mc=w2s(pl.mid[0],pl.mid[1]);               // center facing arrow
    var reach=Math.max(20,pl.len*0.45);
    var fe=w2s(pl.mid[0]+pl.facing[0]*reach, pl.mid[1]+pl.facing[1]*reach);
    var ang=Math.atan2(fe[1]-mc[1],fe[0]-mc[0]);
    ctx.strokeStyle="rgba(217,180,91,.95)"; ctx.lineWidth=2; ctx.beginPath();
    ctx.moveTo(mc[0],mc[1]); ctx.lineTo(fe[0],fe[1]); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(fe[0],fe[1]);
    ctx.lineTo(fe[0]-10*Math.cos(ang-0.4),fe[1]-10*Math.sin(ang-0.4));
    ctx.lineTo(fe[0]-10*Math.cos(ang+0.4),fe[1]-10*Math.sin(ang+0.4));
    ctx.closePath(); ctx.fillStyle="rgba(217,180,91,.95)"; ctx.fill();
    ctx.lineWidth=1;
  }
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
  drawGesture();
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

// ---- command bar (real orders; cheats in a tray) ----
function renderBar(){
  var head=document.getElementById("barhead"), box=document.getElementById("btns");
  var us=live?selUnits(curState()):[];
  if(!us.length){
    head.innerHTML='No unit selected. <span class="hint">left-click AI unit = select · '+
      'shift-click = add · shift-drag = box · right-click = move/attack · '+
      'right-drag = face+width · alt+right = attack ground · left-drag = pan · '+
      'wheel = zoom · R = run</span>';
    box.innerHTML=""; return;
  }
  var ranged=us.some(function(o){return o.u.range>0;});
  var hasAbil=us.some(function(o){return o.u.has_ability;});
  var one=us.length===1?us[0]:null;
  head.innerHTML=(one?('Commanding <b>'+esc(one.u.type||one.u.name)+'</b> ('+esc(one.u.cls||"")+')')
    :('Commanding <b>'+us.length+' units</b>'))+
    (runMode?'  <span style="color:var(--gold)">▶ RUN</span>':'');
  function btn(a,label,cls,on,dis){ return '<button class="tw '+(cls||"")+(on?" on":"")+
    '" data-a="'+a+'"'+(dis?" disabled":"")+'>'+label+'</button>'; }
  var B=[];
  B.push(btn("halt","HALT",""));
  B.push(btn("run","RUN","mode",runMode));
  if(ranged){
    B.push(btn("fire","FIRE AT WILL","",one?!!one.u.faw:false));
    B.push(btn("mlee","MELEE","",one?!!meleeOn[one.key]:false));
  }
  B.push(btn("flee","WITHDRAW",""));
  if(hasAbil){ B.push(btn("abil1","ABILITY 1","")); B.push(btn("abil2","ABILITY 2","")); }
  B.push(btn("release","RELEASE",""));
  B.push(btn("cheats",showCheats?"✕ CHEATS":"⚙ CHEATS","mode",showCheats));
  if(showCheats){
    B.push(btn("fearless","FEARLESS","cheat"));
    B.push(btn("rout","ROUT","cheat"));
    B.push(btn("reset","MORALE RESET","cheat"));
    B.push(btn("taunt","TAUNT","cheat"));
    B.push(btn("kill","KILL","danger"));
  }
  box.innerHTML=B.join("");
  box.querySelectorAll("button").forEach(function(b){
    b.onclick=function(){ barAction(b.dataset.a); };
  });
}
function eachSel(fn){ selUnits(curState()).forEach(fn); }
function barAction(a){
  if(a==="halt"){ eachSel(function(o){ order({verb:"stop",key:o.key}); lastKind[o.key]="move"; }); }
  else if(a==="run"){ runMode=!runMode; }
  else if(a==="fire"){ eachSel(function(o){ if(o.u.range>0) order({verb:"fire",key:o.key,on:o.u.faw?0:1}); }); }
  else if(a==="mlee"){ eachSel(function(o){ meleeOn[o.key]=!meleeOn[o.key]; order({verb:"mlee",key:o.key,on:meleeOn[o.key]?1:0}); }); }
  else if(a==="flee"){ eachSel(function(o){ order({verb:"flee",key:o.key,run:runMode}); lastKind[o.key]="move"; }); }
  else if(a==="abil1"){ var s1=abilSeq++; eachSel(function(o){ if(o.u.has_ability) order({verb:"abil",key:o.key,seq:s1,idx:1}); }); }
  else if(a==="abil2"){ var s2=abilSeq++; eachSel(function(o){ if(o.u.has_ability) order({verb:"abil",key:o.key,seq:s2,idx:2}); }); }
  else if(a==="fearless"){ var f1=actSeq++; eachSel(function(o){ order({verb:"act",key:o.key,id:f1,act:"fearless"}); }); }
  else if(a==="rout"){ var f2=actSeq++; eachSel(function(o){ order({verb:"act",key:o.key,id:f2,act:"rout"}); }); }
  else if(a==="reset"){ var f3=actSeq++; eachSel(function(o){ order({verb:"act",key:o.key,id:f3,act:"default"}); }); }
  else if(a==="taunt"){ var f4=actSeq++; eachSel(function(o){ order({verb:"act",key:o.key,id:f4,act:"taunt"}); }); }
  else if(a==="kill"){ var f5=actSeq++; eachSel(function(o){ order({verb:"act",key:o.key,id:f5,act:"kill"}); }); }
  else if(a==="release"){ eachSel(function(o){ order({verb:"release",key:o.key}); }); sel={}; }
  else if(a==="cheats"){ showCheats=!showCheats; }
  renderBar();
}
function order(body){
  if(!live) return;   // no commanding while viewing a replay
  fetch("/battle_order",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)}).catch(function(){});
}

// ---- orders from map gestures ----
function issueClickOrder(sx,sy,alt){
  var st=curState(), us=selUnits(st); if(!us.length) return;
  var enemy=hitUnit(sx,sy,"player"), w=s2w(sx,sy);
  if(enemy&&enemy.u.pos){                       // right-click enemy = attack it
    us.forEach(function(o){ order({verb:"aunit",key:o.key,x:enemy.u.pos.x,z:enemy.u.pos.z});
      lastKind[o.key]="attack"; });
  } else if(alt){                               // alt+right = attack ground (artillery)
    us.forEach(function(o){ order({verb:"apos",key:o.key,x:w[0],z:w[1],run:runMode});
      lastKind[o.key]="attack"; });
  } else {                                      // move, preserving relative formation
    var cx=0,cz=0; us.forEach(function(o){cx+=o.u.pos.x;cz+=o.u.pos.z;}); cx/=us.length; cz/=us.length;
    us.forEach(function(o){ order({verb:"move",key:o.key,x:w[0]+(o.u.pos.x-cx),z:w[1]+(o.u.pos.z-cz),run:runMode});
      lastKind[o.key]="move"; });
  }
}
// drag draws the FRONT LINE: units spread ALONG the drag, facing perpendicular
// to it. Drag left<->right flips which way they face.
function orientPlan(pw,rw,N){
  var dx=rw[0]-pw[0], dz=rw[1]-pw[1], len=Math.hypot(dx,dz);
  if(len<2||!N) return null;
  var along=[dx/len,dz/len], facing=[along[1],-along[0]];      // face perpendicular
  var bearing=((Math.atan2(facing[0],facing[1])*180/Math.PI)%360+360)%360;
  var mid=[pw[0]+dx/2, pw[1]+dz/2], each=len/N, pts=[];
  for(var i=0;i<N;i++){ var off=(i-(N-1)/2)*each;
    pts.push([mid[0]+along[0]*off, mid[1]+along[1]*off]); }
  return {bearing:bearing, each:each, pts:pts, facing:facing, mid:mid, len:len};
}
function issueOrient(d,sx,sy){
  var st=curState(), us=selUnits(st), N=us.length; if(!N) return;
  var pl=orientPlan([d.wx0,d.wz0], s2w(sx,sy), N);
  if(!pl){ issueClickOrder(sx,sy,d.alt); return; }
  for(var i=0;i<N;i++){
    order({verb:"form",key:us[i].key,x:pl.pts[i][0],z:pl.pts[i][1],
      bearing:pl.bearing,width:pl.each,run:runMode});
    lastKind[us[i].key]="move";
  }
}

// ---- interaction ----
function hitUnit(sx,sy,side){
  var st=curState(), best=null, bd=1e9;
  allUnits(st).forEach(function(o){ if(!o.u.pos) return; if(side&&o.side!==side) return;
    var c=w2s(o.u.pos.x,o.u.pos.z), d=Math.hypot(c[0]-sx,c[1]-sy);
    var f=unitFoot(o.u), r=Math.max(10,f.hw*fitC.s*view.zoom);
    if(d<r && d<bd){bd=d; best=o;} });
  return best;
}
function boxSelect(x0,y0,x1,y1){
  var st=curState(), lo=[Math.min(x0,x1),Math.min(y0,y1)], hi=[Math.max(x0,x1),Math.max(y0,y1)];
  allUnits(st).forEach(function(o){ if(!o.key||!o.u.pos) return;
    var c=w2s(o.u.pos.x,o.u.pos.z);
    if(c[0]>=lo[0]&&c[0]<=hi[0]&&c[1]>=lo[1]&&c[1]<=hi[1]) sel[o.key]=true; });
  renderBar();
}
function relPos(e){ var r=cv.getBoundingClientRect(); return [e.clientX-r.left,e.clientY-r.top]; }
cv.addEventListener("mousedown",function(e){
  var p=relPos(e), w=s2w(p[0],p[1]);
  drag={btn:e.button,sx0:p[0],sy0:p[1],sxl:p[0],syl:p[1],cx:p[0],cy:p[1],
        wx0:w[0],wz0:w[1],moved:false,shift:e.shiftKey,alt:e.altKey};
  if(e.button===2) e.preventDefault();
});
cv.addEventListener("mousemove",function(e){
  var p=relPos(e);
  if(drag){
    if(Math.hypot(p[0]-drag.sx0,p[1]-drag.sy0)>4) drag.moved=true;
    if(drag.btn===0&&!drag.shift){ view.panx+=p[0]-drag.sxl; view.pany+=p[1]-drag.syl; }
    drag.sxl=p[0]; drag.syl=p[1]; drag.cx=p[0]; drag.cy=p[1];
    return;
  }
  var o=hitUnit(p[0],p[1]); hover=o; showSide(o);
});
window.addEventListener("mouseup",function(e){
  if(!drag) return; var d=drag; drag=null;
  var p=relPos(e), sx=p[0], sy=p[1];
  if(d.btn===0){
    if(d.shift){
      if(d.moved){ boxSelect(d.sx0,d.sy0,sx,sy); }
      else { var o=hitUnit(sx,sy); if(o&&o.key){ if(sel[o.key]) delete sel[o.key]; else sel[o.key]=true; renderBar(); } }
    } else if(!d.moved){                        // plain click = select / clear
      var o2=hitUnit(sx,sy); sel={}; if(o2&&o2.key) sel[o2.key]=true; renderBar();
    }                                            // moved = was a pan
  } else if(d.btn===2){
    if(selCount()===0) return;
    if(d.moved) issueOrient(d,sx,sy); else issueClickOrder(sx,sy,d.alt);
  }
});
cv.addEventListener("contextmenu",function(e){e.preventDefault();});
cv.addEventListener("wheel",function(e){
  e.preventDefault();
  var p=relPos(e), before=s2w(p[0],p[1]), f=e.deltaY<0?1.12:1/1.12;
  view.zoom=Math.max(.2,Math.min(12,view.zoom*f));
  computeFit(); var after=s2w(p[0],p[1]), S=fitC.s*view.zoom;
  view.panx+=(after[0]-before[0])*S; view.pany-=(after[1]-before[1])*S;
},{passive:false});
window.addEventListener("keydown",function(e){
  if(e.target&&/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if(e.key==="r"||e.key==="R"){ runMode=!runMode; renderBar(); }
  else if(e.key==="Escape"){ sel={}; renderBar(); }
  else if((e.key==="h"||e.key==="H")&&selCount()){ barAction("halt"); }
});

// ---- status ----
function updateStatus(st){
  var el=document.getElementById("status");
  if(live){
    if(!st){ el.innerHTML='<span style="color:#e8863b">waiting for battle…</span>'; return; }
    el.innerHTML='phase <b>'+esc(st.phase||"?")+'</b> · t <b>'+(st.t!=null?st.t.toFixed(0):"?")+
      's</b> · units <b>'+allUnits(st).length+'</b> · sel <b>'+selCount()+
      '</b> · age '+battleAge+'s';
  } else {
    el.innerHTML='<span style="color:#d9b45b">REPLAY</span> '+esc(rep.file||"")+' · frame <b>'+
      (rep.i+1)+'/'+rep.frames.length+'</b>';
  }
}

// ---- live polling ----
function poll(){
  if(!live) return;
  fetch("/state",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
    var inc=d.battle; battleAge=(d.battle_age==null?99:d.battle_age);
    if(!inc){ state=null; }
    else if(allUnits(inc).length>0 || !state || allUnits(state).length===0 || inc.phase==="complete"){
      state=inc;                    // accept real frames, first frame, or battle-end
    }                               // else: transient empty frame -> keep last good state
    if(!geom && inc) loadGeom();   // written once per battle; retry until it lands
    if(selCount()) renderBar();
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

// ---- replay ----
function setLive(on){
  live=on; sel={}; hover=null;
  document.getElementById("mLive").className=on?"on":"";
  document.getElementById("mReplay").className=on?"":"on";
  document.getElementById("rep").className=on?"":"show";
  document.getElementById("barhead").style.opacity=on?1:.5;
  if(on){ loadGeom(); loadStats(); } else { loadReplays(); }
  renderBar();
}
function loadReplays(){
  fetch("/replays").then(function(r){return r.json();}).then(function(list){
    var s=document.getElementById("repSel");
    s.innerHTML=list.map(function(r){
      return '<option value="'+esc(r.file)+'">'+esc(r.file)+' ('+Math.round(r.size/1024)+'kB)</option>';
    }).join("");
    if(list.length){ s.value=list[0].file; pickReplay(list[0].file); }
    else { document.getElementById("repT").textContent="no recordings yet"; }
  }).catch(function(){});
}
function pickReplay(file){
  fetch("/replay?file="+encodeURIComponent(file)).then(function(r){return r.text();}).then(function(txt){
    rep={frames:[],cmds:[],geom:null,i:0,playing:false,file:file};
    txt.split("\n").forEach(function(ln){ ln=ln.trim(); if(!ln) return;
      var o; try{o=JSON.parse(ln);}catch(e){return;}
      if(o.kind==="geometry") rep.geom=o;
      else if(o.k==="c") rep.cmds.push(o);
      else if(o.kind==="battle"&&o.alliances) rep.frames.push(o);
    });
    var tl=document.getElementById("tl");
    tl.max=Math.max(0,rep.frames.length-1); tl.value=0; rep.i=0;
    view={zoom:1,panx:0,pany:0};
    document.getElementById("repT").textContent=rep.frames.length+" frames, "+rep.cmds.length+" cmds";
  }).catch(function(){});
}
document.getElementById("mLive").onclick=function(){setLive(true);};
document.getElementById("mReplay").onclick=function(){setLive(false);};
document.getElementById("repSel").onchange=function(){pickReplay(this.value);};
document.getElementById("tl").oninput=function(){ rep.i=+this.value; };
document.getElementById("repPlay").onclick=function(){
  rep.playing=!rep.playing; this.textContent=rep.playing?"Pause":"Play";
};
setInterval(function(){
  if(!live&&rep.playing&&rep.frames.length){
    rep.i=(rep.i+1)%rep.frames.length; document.getElementById("tl").value=rep.i;
  }
},500);

loadGeom(); loadStats(); renderBar(); setInterval(poll,500); poll(); render();
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
        elif p == "/state":
            battle, age = read_json(BATTLE_FILE)
            self._send(json.dumps({"battle": battle, "battle_age": age}))
        elif p == "/geometry":
            geo, age = read_json(GEOMETRY_FILE)
            self._send(json.dumps(geo if geo else {}))
        elif p == "/unitstats":
            st, age = read_json(STATS_FILE)
            self._send(json.dumps(st if st else {}))
        elif p == "/replays":
            self._send(json.dumps(list_replays()))
        elif p == "/replay":
            q = parse_qs(parsed.query)
            txt = read_replay((q.get("file") or [""])[0])
            if txt is None:
                self._send("", "text/plain", 404)
            else:
                self._send(txt, "text/plain; charset=utf-8")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        if self.path != "/battle_order":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            queue_battle_order(json.loads(self.rfile.read(n)))
            self._send(json.dumps({"ok": True}))
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
