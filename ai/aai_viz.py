"""Live 2D visualization of Attila game state.

Serves http://localhost:8765/ — a canvas page that polls /state twice a
second and reconstructs whatever the game is doing: the campaign map
(factions, armies, settlements, camera) or a running battle (every unit
on both sides, oriented and scaled, plus the battle camera). Reads the
JSON files written by the mod's state exporters (src/*/state_json.lua).

Spawned at game boot by src/frontend/spawn_viz.lua; duplicate instances
exit because the port is bound. Safe to run by hand too:
  python ai/aai_viz.py
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765


def find_game_dir():
    cwd = os.getcwd()
    if os.path.isfile(os.path.join(cwd, "data", "attila_ai_log.txt")):
        return cwd
    return r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila"


GAME = find_game_dir()
DATA = os.path.join(GAME, "data")
FILES = {
    "campaign": os.path.join(DATA, "aai_campaign.json"),
    "battle": os.path.join(DATA, "aai_battle.json"),
}
LOG_PATH = os.path.join(DATA, "aai_viz_log.txt")
ORDERS_OUT = os.path.join(DATA, "aai_campaign_orders.txt")
BATTLE_ORDERS_OUT = os.path.join(DATA, "aai_orders.txt")

_pending = {}   # campaign: cqi -> (order line, wall time)
_bpending = {}  # battle: squad -> (order line, wall time)

_cache = {}  # key -> (data, mtime)


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + msg + "\n")
    except OSError:
        pass


def read_state(key):
    """Latest parsed JSON + age in seconds; tolerates missing/partial files."""
    path = FILES[key]
    try:
        mtime = os.path.getmtime(path)
        cached = _cache.get(key)
        if not cached or cached[1] != mtime:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                _cache[key] = (json.load(f), mtime)
    except (OSError, ValueError):
        pass  # keep last good snapshot
    cached = _cache.get(key)
    if not cached:
        return None, None
    return cached[0], round(time.time() - cached[1], 1)


def _least_squares(pairs):
    n = len(pairs)
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] * p[0] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    d = n * sxx - sx * sx
    if n < 2 or abs(d) < 1e-9:
        return None
    a = (n * sxy - sx * sy) / d
    return a, (sy - a * sx) / n


def fit_display_to_logical(campaign):
    """Affine per-axis fit from every mark that carries both coordinate
    systems (forces and settlements)."""
    if not campaign:
        return None
    xs, ys = [], []
    for fac in campaign.get("factions", []):
        for m in fac.get("forces", []) + fac.get("settlements", []):
            if all(isinstance(m.get(k), (int, float)) for k in ("x", "y", "lx", "ly")):
                xs.append((m["x"], m["lx"]))
                ys.append((m["y"], m["ly"]))
    fx, fy = _least_squares(xs), _least_squares(ys)
    return (fx, fy) if fx and fy else None


def queue_order(req):
    campaign, _age = read_state("campaign")
    fit = fit_display_to_logical(campaign)
    if not fit:
        raise ValueError("no display-to-logical fit yet (need campaign state)")
    (ax, bx), (ay, by) = fit
    lx = ax * float(req["x"]) + bx
    ly = ay * float(req["y"]) + by
    now = time.time()
    _pending[int(req["cqi"])] = (
        "move %d %.1f %.1f %s" % (int(req["cqi"]), lx, ly, req["faction"]), now)
    for k in [k for k, v in _pending.items() if now - v[1] > 10]:
        del _pending[k]
    tmp = ORDERS_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("seq %d\n" % int(now * 1000))
        for line, _ts in _pending.values():
            f.write(line + "\n")
    os.replace(tmp, ORDERS_OUT)
    log("order: cqi=%s faction=%s -> logical %.1f, %.1f" %
        (req["cqi"], req["faction"], lx, ly))
    return lx, ly


def queue_battle_order(req):
    now = time.time()
    key = str(req["key"])
    _bpending[key] = (
        "move %s %.1f %.1f run" % (key, float(req["x"]), float(req["z"])), now)
    # standing orders persist game-side; the file only needs recent lines
    for k in [k for k, v in _bpending.items() if now - v[1] > 10]:
        del _bpending[k]
    tmp = BATTLE_ORDERS_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("seq %d\n" % int(now * 1000))
        for line, _ts in _bpending.values():
            f.write(line + "\n")
    os.replace(tmp, BATTLE_ORDERS_OUT)
    log("battle order: " + _bpending[key][0])


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Attila AI — live state</title>
<style>
  html, body { margin: 0; height: 100%; background: #0d0d0d; overflow: hidden;
    font: 13px -apple-system, "Segoe UI", sans-serif; }
  canvas { display: block; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<script>
"use strict";
// palette (validated reference set, dark mode)
const SURFACE = "#1a1a19", PAGE_BG = "#0d0d0d";
const INK = "#ffffff", INK2 = "#c3c2b7", MUTED = "#898781", GRID = "#2c2c2a";
const PLAYER = "#3987e5", ENEMY = "#e66767", ROUTING = "#ec835a";

const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let state = null;          // last /state payload
let mouse = null;          // {x, y} css px
let hittable = [];         // [{x, y, r, lines: []}] rebuilt each frame
let viewMode = "waiting";
let drag = null;           // {order, sx, sy, cx, cy}
let toast = null;          // {text, until}
let campInv = null;        // screen -> world (display coords) for campaign
let battleInv = null;      // screen -> world (x,z ground plane) for battle

function resize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = innerWidth * dpr;
  canvas.height = innerHeight * dpr;
  canvas.style.width = innerWidth + "px";
  canvas.style.height = innerHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
addEventListener("resize", resize);
canvas.addEventListener("mousemove", e => {
  mouse = { x: e.clientX, y: e.clientY };
  if (drag) { drag.cx = e.clientX; drag.cy = e.clientY; }
  draw();
});
canvas.addEventListener("mouseleave", () => { mouse = null; drag = null; draw(); });
canvas.addEventListener("mousedown", e => {
  if (viewMode !== "battle") return;
  for (const h of hittable) {
    if (h.order && Math.hypot(h.x - e.clientX, h.y - e.clientY) <= h.r + 4) {
      drag = { mode: viewMode, order: h.order,
        sx: h.x, sy: h.y, cx: e.clientX, cy: e.clientY };
      return;
    }
  }
});
canvas.addEventListener("mouseup", e => {
  if (!drag) return;
  const d = drag;
  drag = null;
  if (Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 10) sendOrder(d, e.clientX, e.clientY);
  draw();
});

async function sendOrder(d, sx, sy) {
  const inv = d.mode === "battle" ? battleInv : campInv;
  if (!inv) return;
  const w = inv({ x: sx, y: sy });
  const url = d.mode === "battle" ? "/battle_order" : "/order";
  const payload = d.mode === "battle"
    ? { key: d.order.key, x: w.x, z: w.y }
    : { cqi: d.order.cqi, faction: d.order.faction, x: w.x, y: w.y };
  try {
    const r = await fetch(url, { method: "POST", body: JSON.stringify(payload) });
    const j = await r.json();
    toast = { until: Date.now() + 4000, text: j.ok
      ? "move order sent \\u2192 " + (d.order.label || "unit")
      : "order FAILED: " + j.error };
  } catch (e) {
    toast = { text: "order failed: server unreachable", until: Date.now() + 4000 };
  }
  draw();
}

async function poll() {
  try {
    const r = await fetch("/state");
    state = await r.json();
  } catch (e) { /* server gone; keep last frame */ }
  draw();
}
setInterval(poll, 500);

// --- projection: fit world points into the canvas, world "up" = screen up
function fitter(points, pad) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
  }
  if (minX > maxX) { minX = 0; maxX = 1; minY = 0; maxY = 1; }
  const spanX = Math.max(maxX - minX, 1e-6), spanY = Math.max(maxY - minY, 1e-6);
  const s = Math.min((innerWidth - 2 * pad) / spanX, (innerHeight - 2 * pad) / spanY);
  const ox = (innerWidth - spanX * s) / 2, oy = (innerHeight - spanY * s) / 2;
  const f = p => ({ x: ox + (p.x - minX) * s, y: innerHeight - oy - (p.y - minY) * s });
  f.inv = q => ({ x: minX + (q.x - ox) / s, y: minY + (innerHeight - oy - q.y) / s });
  return f;
}

function clear() {
  ctx.fillStyle = PAGE_BG;
  ctx.fillRect(0, 0, innerWidth, innerHeight);
  hittable = [];
}

function hud(lines) {
  ctx.fillStyle = INK;
  ctx.font = "600 15px -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText(lines[0], 16, 26);
  ctx.font = "13px -apple-system, 'Segoe UI', sans-serif";
  ctx.fillStyle = INK2;
  for (let i = 1; i < lines.length; i++) ctx.fillText(lines[i], 16, 26 + i * 18);
}

function legend(items) {
  let y = innerHeight - 16 - (items.length - 1) * 18;
  for (const [color, hollow, label] of items) {
    ctx.beginPath();
    ctx.arc(22, y - 4, 5, 0, 7);
    if (hollow) { ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke(); }
    else { ctx.fillStyle = color; ctx.fill(); }
    ctx.fillStyle = INK2;
    ctx.font = "12px -apple-system, 'Segoe UI', sans-serif";
    ctx.fillText(label, 34, y);
    y += 18;
  }
}

function crosshair(pt, label) {
  ctx.strokeStyle = INK;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pt.x - 8, pt.y); ctx.lineTo(pt.x + 8, pt.y);
  ctx.moveTo(pt.x, pt.y - 8); ctx.lineTo(pt.x, pt.y + 8);
  ctx.stroke();
  ctx.fillStyle = MUTED;
  ctx.font = "11px -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText(label, pt.x + 10, pt.y - 6);
}

function tooltip() {
  if (!mouse) return;
  let best = null, bestD = 15;
  for (const h of hittable) {
    const d = Math.hypot(h.x - mouse.x, h.y - mouse.y) - h.r;
    if (d < bestD) { bestD = d; best = h; }
  }
  if (!best) return;
  ctx.font = "12px -apple-system, 'Segoe UI', sans-serif";
  const w = Math.max(...best.lines.map(l => ctx.measureText(l).width)) + 16;
  const hgt = best.lines.length * 16 + 10;
  let tx = best.x + 14, ty = best.y - hgt / 2;
  if (tx + w > innerWidth - 8) tx = best.x - 14 - w;
  ty = Math.max(8, Math.min(ty, innerHeight - hgt - 8));
  ctx.fillStyle = "rgba(26,26,25,0.95)";
  ctx.strokeStyle = "rgba(255,255,255,0.10)";
  ctx.beginPath();
  ctx.roundRect(tx, ty, w, hgt, 4);
  ctx.fill(); ctx.stroke();
  best.lines.forEach((l, i) => {
    ctx.fillStyle = i === 0 ? INK : INK2;
    ctx.fillText(l, tx + 8, ty + 18 + i * 16);
  });
}

// --- battle view ---------------------------------------------------
function drawBattle(b) {
  clear();
  viewMode = "battle";
  const units = [];
  (b.alliances || []).forEach((al, ai) => {
    const isEnemy = (ai + 1) !== (b.player_alliance || 1);
    (al.armies || []).forEach((army, mi) => {
      (army.units || []).forEach(u => {
        if (u.pos && typeof u.pos.x === "number")
          units.push({ u, alliance: ai + 1,
            key: (isEnemy && u.i) ? (ai + 1) + ":" + (mi + 1) + ":" + u.i : null });
      });
    });
  });
  const pts = units.map(w => ({ x: w.u.pos.x, y: w.u.pos.z }));
  const cam = b.camera && b.camera.pos;
  const tgt = b.camera && b.camera.target;
  if (tgt && typeof tgt.x === "number") pts.push({ x: tgt.x, y: tgt.z });
  const P = fitter(pts, 60);
  battleInv = P.inv;

  // faint ground grid every 100 m
  if (units.length) {
    ctx.strokeStyle = GRID; ctx.lineWidth = 1;
    const xs = units.map(w => w.u.pos.x), zs = units.map(w => w.u.pos.z);
    const x0 = Math.floor(Math.min(...xs) / 100) * 100, x1 = Math.max(...xs);
    const z0 = Math.floor(Math.min(...zs) / 100) * 100, z1 = Math.max(...zs);
    for (let gx = x0; gx <= x1 + 100; gx += 100) {
      const a = P({ x: gx, y: z0 - 100 }), c = P({ x: gx, y: z1 + 100 });
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(c.x, c.y); ctx.stroke();
    }
    for (let gz = z0; gz <= z1 + 100; gz += 100) {
      const a = P({ x: x0 - 100, y: gz }), c = P({ x: x1 + 100, y: gz });
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(c.x, c.y); ctx.stroke();
    }
  }

  let mine = 0, theirs = 0;
  for (const w of units) {
    const u = w.u;
    const isPlayer = w.alliance === (b.player_alliance || 1);
    if (isPlayer) mine++; else theirs++;
    const pt = P({ x: u.pos.x, y: u.pos.z });
    const frac = u.men0 ? Math.max(0, Math.min(1, u.men / u.men0)) : 1;
    const r = 6 + 5 * Math.sqrt(frac);
    let color = isPlayer ? PLAYER : ENEMY;
    if (u.routing || u.shattered) color = ROUTING;

    ctx.save();
    ctx.translate(pt.x, pt.y);
    ctx.rotate((u.bearing || 0) * Math.PI / 180);
    ctx.beginPath();
    if (u.cavalry) {  // diamond
      ctx.moveTo(0, -r); ctx.lineTo(r * 0.7, 0); ctx.lineTo(0, r); ctx.lineTo(-r * 0.7, 0);
    } else {          // triangle, apex = facing
      ctx.moveTo(0, -r); ctx.lineTo(r * 0.8, r * 0.8); ctx.lineTo(-r * 0.8, r * 0.8);
    }
    ctx.closePath();
    if (u.routing || u.shattered) {   // hollow = broken
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
      if (u.shattered) {
        ctx.beginPath();
        ctx.moveTo(-r * 0.5, -r * 0.5); ctx.lineTo(r * 0.5, r * 0.5);
        ctx.moveTo(r * 0.5, -r * 0.5); ctx.lineTo(-r * 0.5, r * 0.5);
        ctx.stroke();
      }
    } else {
      ctx.fillStyle = color; ctx.fill();
      ctx.strokeStyle = PAGE_BG; ctx.lineWidth = 1; ctx.stroke();  // surface ring
    }
    ctx.restore();

    if (u.ordered && typeof u.ordered.x === "number") {
      const op = P({ x: u.ordered.x, y: u.ordered.z });
      if (Math.hypot(op.x - pt.x, op.y - pt.y) > 6) {
        ctx.strokeStyle = MUTED; ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(pt.x, pt.y); ctx.lineTo(op.x, op.y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.strokeRect(op.x - 2.5, op.y - 2.5, 5, 5);
      }
    }
    const flags = [u.cavalry ? "cavalry" : "", u.melee ? "IN MELEE" : "",
      u.moving ? "moving" : "", u.idle ? "idle" : "", u.routing ? "ROUTING" : "",
      u.shattered ? "SHATTERED" : ""].filter(Boolean).join(" \\u00b7 ");
    const lines = [u.name || "?"];
    if (u.type) lines.push(u.type);
    lines.push("men " + u.men + "/" + u.men0 + (u.ammo ? "  ammo " + u.ammo : ""));
    if (u.xp != null || u.fatigue != null)
      lines.push((u.xp != null ? "xp " + u.xp + "  " : "") +
                 (u.fatigue != null ? "fatigue " + u.fatigue : ""));
    lines.push("pos " + u.pos.x.toFixed(0) + ", " + u.pos.z.toFixed(0) +
      "  brg " + (u.bearing || 0).toFixed(0) + "\\u00b0");
    if (u.ordered && typeof u.ordered.x === "number")
      lines.push("ordered to " + u.ordered.x.toFixed(0) + ", " + u.ordered.z.toFixed(0));
    if (flags) lines.push(flags);
    if (w.key) lines.push("drag to move (" + w.key + ")");
    hittable.push({ x: pt.x, y: pt.y, r, lines,
      order: w.key ? { key: w.key, label: (u.type || "unit") + " " + w.key } : null });
  }

  if (drag) {
    ctx.strokeStyle = INK;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(drag.sx, drag.sy); ctx.lineTo(drag.cx, drag.cy); ctx.stroke();
    const ang = Math.atan2(drag.cy - drag.sy, drag.cx - drag.sx);
    ctx.beginPath();
    ctx.moveTo(drag.cx, drag.cy);
    ctx.lineTo(drag.cx - 10 * Math.cos(ang - 0.4), drag.cy - 10 * Math.sin(ang - 0.4));
    ctx.moveTo(drag.cx, drag.cy);
    ctx.lineTo(drag.cx - 10 * Math.cos(ang + 0.4), drag.cy - 10 * Math.sin(ang + 0.4));
    ctx.stroke();
  }

  if (cam && tgt && typeof cam.x === "number" && typeof tgt.x === "number") {
    const cp = P({ x: cam.x, y: cam.z }), tp = P({ x: tgt.x, y: tgt.z });
    ctx.strokeStyle = MUTED; ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(cp.x, cp.y); ctx.lineTo(tp.x, tp.y); ctx.stroke();
    ctx.setLineDash([]);
    crosshair(tp, "camera");
  }

  hud(["BATTLE  \\u00b7  " + (b.phase || "?") + "  \\u00b7  t=" + (b.t || 0).toFixed(0) + "s",
       "your units: " + mine + "    enemy units: " + theirs,
       "state age " + state.battle_age + "s",
       "drag a red unit to order it (works once fighting starts)"]);
  legend([[PLAYER, false, "your alliance"], [ENEMY, false, "enemy"],
          [ROUTING, true, "routing / shattered (hollow)"],
          [MUTED, true, "ordered destination (dashed)"]]);
  tooltip();
}

// --- campaign view -------------------------------------------------
function drawCampaign(c) {
  clear();
  viewMode = "campaign";
  const marks = [];
  for (const fac of (c.factions || [])) {
    for (const s of (fac.settlements || []))
      marks.push({ x: s.x, y: s.y, kind: "settlement", fac, name: s.name });
    for (const fo of (fac.forces || []))
      marks.push({ x: fo.x, y: fo.y, kind: "force", fac, units: fo.units, cqi: fo.cqi });
  }
  const pts = marks.map(m => ({ x: m.x, y: m.y }));
  if (c.camera && typeof c.camera.x === "number") pts.push({ x: c.camera.x, y: c.camera.y });
  const P = fitter(pts, 50);
  campInv = P.inv;

  for (const m of marks) {           // settlements under forces
    if (m.kind !== "settlement") continue;
    const pt = P(m);
    const color = m.fac.human ? PLAYER : MUTED;
    ctx.fillStyle = color;
    ctx.fillRect(pt.x - 4, pt.y - 4, 8, 8);
    ctx.strokeStyle = PAGE_BG; ctx.lineWidth = 1;
    ctx.strokeRect(pt.x - 4, pt.y - 4, 8, 8);
    hittable.push({ x: pt.x, y: pt.y, r: 6, lines: [
      (m.name || "settlement").replace(/^att_reg_/, ""), m.fac.name] });
  }
  for (const m of marks) {
    if (m.kind !== "force") continue;
    const pt = P(m);
    const r = 4 + Math.min(6, (m.units || 1) * 0.35);
    ctx.beginPath(); ctx.arc(pt.x, pt.y, r, 0, 7);
    ctx.fillStyle = m.fac.human ? PLAYER : MUTED;
    ctx.fill();
    ctx.strokeStyle = PAGE_BG; ctx.lineWidth = 1; ctx.stroke();
    hittable.push({ x: pt.x, y: pt.y, r, order:
      (m.cqi != null ? { cqi: m.cqi, faction: m.fac.name } : null), lines: [
      "army \\u00b7 " + (m.units || "?") + " units", m.fac.name,
      m.cqi != null ? "drag to order a march" : "no cqi"] });
  }

  if (c.camera && typeof c.camera.x === "number")
    crosshair(P(c.camera), "camera  zoom " + (c.camera.zoom || 0).toFixed(1));

  const nFac = (c.factions || []).length;
  hud(["CAMPAIGN  \\u00b7  turn " + (c.turn == null ? "?" : c.turn),
       nFac + " factions  \\u00b7  " + marks.length + " marks",
       "state age " + state.campaign_age + "s"]);
  legend([[PLAYER, false, "your faction"], [MUTED, false, "everyone else"]]);
  tooltip();
}

function drawWaiting() {
  clear();
  viewMode = "waiting";
  ctx.fillStyle = INK2;
  ctx.font = "15px -apple-system, 'Segoe UI', sans-serif";
  ctx.textAlign = "center";
  const ba = state && state.battle_age, ca = state && state.campaign_age;
  ctx.fillText("waiting for game state\\u2026", innerWidth / 2, innerHeight / 2 - 10);
  ctx.fillStyle = MUTED;
  ctx.font = "12px -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText("battle: " + (ba == null ? "never seen" : ba + "s old") +
    "    campaign: " + (ca == null ? "never seen" : ca + "s old"),
    innerWidth / 2, innerHeight / 2 + 14);
  ctx.textAlign = "left";
}

function draw() {
  if (!state) drawWaiting();
  else if (state.battle && state.battle_age != null && state.battle_age < 5 &&
      state.battle.phase !== "complete") drawBattle(state.battle);
  else if (state.campaign && state.campaign_age != null && state.campaign_age < 8)
    drawCampaign(state.campaign);
  else drawWaiting();
  if (toast && Date.now() < toast.until) {
    ctx.fillStyle = INK;
    ctx.font = "13px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(toast.text, innerWidth / 2, innerHeight - 40);
    ctx.textAlign = "left";
  }
}

resize();
poll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/state":
            campaign, c_age = read_state("campaign")
            battle, b_age = read_state("battle")
            body = json.dumps({
                "campaign": campaign, "campaign_age": c_age,
                "battle": battle, "battle_age": b_age,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path not in ("/order", "/battle_order"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n))
            if self.path == "/battle_order":
                queue_battle_order(req)
                body = json.dumps({"ok": True}).encode()
            else:
                lx, ly = queue_order(req)
                body = json.dumps({"ok": True, "lx": round(lx, 1), "ly": round(ly, 1)}).encode()
            code = 200
        except Exception as e:
            body = json.dumps({"ok": False, "error": str(e)}).encode()
            code = 400
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    # stock handle_error prints to sys.stderr, which is None under
    # pythonw — the print raises and kills the accept loop, leaving a
    # zombie process holding no port. Log to file instead.
    def handle_error(self, request, client_address):
        import sys
        info = sys.exc_info()[1]
        log("request error from %s: %r" % (client_address, info))


def main():
    try:
        server = QuietServer(("127.0.0.1", PORT), Handler)
    except OSError:
        return  # already running — the whole point of the port guard
    log("viz server up: http://localhost:%d  game=%s" % (PORT, GAME))
    server.serve_forever()


if __name__ == "__main__":
    main()
