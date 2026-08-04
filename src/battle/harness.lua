-------------------------------------------------------------------------
--	INTERACTIVE TEST HARNESS: the cockpit's "capability test" backend.
--	The viz server writes ONE order at a time to data/aai_test_order.txt
--	(seq-gated); this module applies it to the named AI unit and acks to
--	data/aai_test_ack.json. Theo drives it from the /harness page and
--	checks capabilities off by eye.
--
--	DEFAULT-OFF: self-gates on data/aai_harness_on.txt.
--
--	LAWS (SCRIPTING.md -- all engine work only from the pump, which rides
--	vanilla's tick via publish): no timers, no engine calls from events,
--	engine-touching verbs CONFLICT-PHASE ONLY (deployment execution froze
--	the dispatch 2026-07-28), everything pcall'd, buildings untouched.
-------------------------------------------------------------------------
local M = {};

local json = require "aai_json";

local FLAG = "data/aai_harness_on.txt";
local ORDER = "data/aai_test_order.txt";
local ACK = "data/aai_test_ack.json";

local corel = nil;
local bm, battle, bridge = nil, nil, nil;
local mkvec = nil;
local phase = "loading";
local ticks = 0;
local last_seq = -1;
local armed_cache, armed_check = nil, 0;

-- FREEZE: park the whole AI side so tests happen on a still field.
-- ON by default while armed; toggled from the cockpit ("freeze all 0|1").
-- Per pump: re-take control of every AI unit (take is not durable), halt
-- each once at seizure, re-halt any that starts moving again -- EXCEPT
-- units with a standing harness order (the `ordered` set), whose moves
-- must play out. (2026-07-29 fix: this was a single last_target slot, so
-- ordering unit B halted unit A's in-flight move on the next pump --
-- Theo's "new orders cancel old orders". Now per-unit.) Toggling freeze
-- clears the set: FROZEN = full re-park, new orders re-exempt.
local freeze_on = true;
local seized = {};
local ordered = {};

-- live command stream (order-type capability): the probe's command handler
-- pushes {nm=,extra=} rows into _G.aai_cmd_rows; we drain them here and
-- surface the recent ones to the cockpit as data/aai_cmds.json
local CMDS = "data/aai_cmds.json";
local cmd_seen = 0;
local cmd_recent = {};
local cmd_names = {};	-- whole-battle census: order name -> count (never
			-- windowed, so rare orders like withdraw are never lost)

-- battlefield probe outputs + handle caches (bld/aeq indices are the handles
-- the sieges verbs take -- run the probe verb first, then act by index)
local BLD = "data/aai_bld.json";
local AEQ = "data/aai_aeq.json";
local CAPEV = "data/aai_capev.json";
local bld_cache = {};
local bld_rows = {};	-- accumulated dump rows across chunked bld calls
local bld_pos = 0;	-- registry cursor: how far the chunked scan has walked
local bis_next = 1;	-- bldstep bisect ladder: auto-advancing step cursor
local bis_list = nil;	-- ladder handles, acquired lazily by the steps
local bis_b1 = nil;
local bis_b2 = nil;
local aeq_cache = {};
local cap_events = {};
local cap_dirty = false;

local function log(text)
	corel.log("HARNESS " .. tostring(text));
end;

-- VM-only metatable census (debug.getmetatable pierces the __metatable
-- decoy; no engine code runs) -- the bldstep ladder uses it to look for a
-- touch-free cold-slot signature
local function bis_mtinfo(o)
	local keys = 0;
	pcall(function()
		local mt = debug.getmetatable(o);
		if type(mt) == "table" then
			for _ in pairs(mt) do
				keys = keys + 1;
			end;
		elseif mt ~= nil then
			keys = -1;
		end;
	end);
	return keys;
end;

local function armed()
	if ticks - armed_check >= 5 or armed_cache == nil then
		armed_check = ticks;
		local f = io.open(FLAG, "r");
		armed_cache = (f ~= nil);
		if f then
			f:close();
		end;
	end;
	return armed_cache;
end;

local function ack(seq, line, ok, err, val)
	pcall(function()
		json.write(ACK, {
			seq = seq, line = line, ok = ok and true or false,
			err = err and tostring(err) or nil,
			val = (val ~= nil) and tostring(val) or nil,
			phase = phase, clock = os.clock(),
		});
	end);
	log("[" .. tostring(seq) .. "] " .. tostring(line) .. " -> " ..
		(ok and ("OK" .. ((val ~= nil) and (" = " .. tostring(val)) or ""))
		or ("ERR " .. tostring(err))));
end;

-- squad-cache lookup by "alliance:army:unit" key (AI-side units only)
local function squad(key)
	local cache = bridge.sync_squads();
	return cache and cache[key] or nil;
end;

--------------------------------------------------------------------------
--	verb table: fn(e, a) where e = {unit=, uc=}, a = arg list (strings).
--	Each returns nothing (error -> pcall catches it in apply()).
--------------------------------------------------------------------------
local function n(x)
	return tonumber(x) or 0;
end;

local function b(x)
	return x == "1" or x == "true";
end;

-- numeric-coerced arg list for pass-through engine calls with un-RE'd arity
local function vargs(a, from)
	local out = {};
	for i = (from or 1), #a do
		out[#out + 1] = tonumber(a[i]) or a[i];
	end;
	return out;
end;

--------------------------------------------------------------------------
--	GLOBAL verbs (no unit key; args start at parts[2]): battlefield PROBES
--	only (elevation / buildings / assault equipment / victory points /
--	ships) + the freeze toggle. All pcall'd in apply(); a wrong method name
--	or arity acks as ERR (that ack IS the capability verdict signal --
--	never a silent no-op).
--	(2026-07-29 cull, Theo's order: every battle-level WRITE lever removed
--	-- speed/clock/countdown/end-battle/AI-plan-hints/planner/army-quit/
--	projectile/naval-mine/building-fire. Unit-level control is the scope.)
--------------------------------------------------------------------------
local GLOBALS;
GLOBALS = {
	freeze = function(a)
		-- cockpit sends "freeze all 0|1" -> the value is the LAST token
		local v = a[#a];
		freeze_on = (v == "1" or v == "true");
		ordered = {};	-- both directions: re-freeze means full park
		if not freeze_on then
			local cache = bridge.sync_squads();
			for k, e in pairs(cache or {}) do
				if not e.pre then
					pcall(function() bridge.release(e.uc); end);
				end;
			end;
			seized = {};
		end;
	end,
	-- terrain: sample ground elevation at (x,z) -> comes back as the ack val
	elev = function(a)
		if not mkvec then error("no vector ctor"); end;
		local p = mkvec(tonumber(a[1]) or 0, tonumber(a[2]) or 0);
		if not p then error("vector ctor returned nil"); end;
		return p:get_y();
	end,
	-- BUILDINGS PROBE -- KNOWN DISPATCH KILLER (the SCRIPTING.md buildings
	-- quarantine). 07-31 EVENING VERDICT: detection is NOT protection. The
	-- cold-guard run touched SEVEN calls total (count, item x2, name x2,
	-- one central_position), bailed correctly at the first cold entry --
	-- and the vanilla tick dispatch still died on that very tick (vt froze
	-- at 1075; the command-handler dispatch + the Lua VM lived on for 6+
	-- min). Cold is PER-ENTRY, not per-surface: item(1) answered a real
	-- name ("western_villa_wall_door") while item(2) was the poison.
	-- 07-31 NIGHT, the full picture: warmth is a LIVE, REVERSIBLE,
	-- per-entity state. Same battle, same slot: cold at vt ~245-1075
	-- (battle start), fully warm at vt 3200-4429 (ladder, census
	-- warm=250/250), COLD AGAIN at vt ~11920 (scan died) -- entries
	-- revert. Best model: script binding tracks entity RESIDENCY in the
	-- engine's streaming/destruction system (near camera/action = bound;
	-- idle/far = evicted); the 56-min fought battle stayed scannable
	-- because everything was hot everywhere. NO TIMING RULE IS SAFE.
	-- FINAL VERDICT (07-31 20:20 run): the Lua route is CLOSED -- the
	-- names-only walk (zero object calls) still killed the dispatch at
	-- the first cold entry, and the cold entry's metatable measured 10
	-- keys, IDENTICAL to warm -- cold is undetectable from the VM and
	-- any touch is lethal. Buildings go native/static (SIEGE_GEOMETRY.md
	-- + native map-id). This verb stays for deliberate throwaway
	-- experiments only. Args: [1] chunk size (default 250), [2] mode:
	-- "lite" (name+pos) | "names" (name only) | default full.
	bld = function(a)
		local chunk = tonumber(a and a[1]) or 250;
		if chunk < 1 then chunk = 1; end;
		-- mode: "full" (all fields) | "lite" (name+position) | "names"
		-- (name ONLY -- no object-returning calls at all). names mode is
		-- the object-getter-law test: in every kill the scan had called
		-- central_position() on a half-cold entry (name warm, pos cold --
		-- entry 1 both times); if object getters are the poison (the
		-- 07-30 events-context precedent), a names-only walk survives a
		-- cold registry and becomes the safe enumerator.
		local mode = (a and tostring(a[2])) or "full";
		if mode ~= "lite" and mode ~= "names" and mode ~= "v1" then
			mode = "full";
		end;
		local list = battle:buildings();
		local ok_t, total = pcall(function() return list:count(); end);
		if not ok_t or type(total) ~= "number" then total = 0; end;
		if mode == "v1" then
			-- The V1-mechanics scan: blind raw pcalls, full fields, NO
			-- metatable census, NO cold bail, NO breadcrumbs -- the ONLY
			-- call pattern that ever survived FRESH battles (01:43 twice
			-- at ~70 s, the 3-hr 22:29 battle x5, and the byte-faithful
			-- 300-clamp replica twice on the 08-01 fort battle, registry
			-- warm at ~85 s, feed alive after). 08-01 EXTENSION (Theo:
			-- "why is it not going farther"): cursor-driven -- each click
			-- walks the NEXT <chunk> entries with the same blind
			-- mechanics, restarting after the end. If a DEEP chunk kills
			-- on an otherwise-warm map, the poison frontier (the empty
			-- pool-slot region past the real architecture -- what the
			-- all-29k walk crossed when it died at 01:55) is localized.
			local scan = (type(total) == "number") and total or 0;
			if bld_pos >= scan then	-- done (or empty): restart fresh
				bld_pos = 0; bld_cache = {}; bld_rows = {};
			end;
			local from = bld_pos + 1;
			local stop = bld_pos + chunk;
			if stop > scan then stop = scan; end;
			for i = from, stop do
				local bo = list:item(i);
				if bo then
					-- keyed by REGISTRY index: rows carry e.i = i, so the
					-- cockpit's click-to-attack (battk e.i) hits exactly
					-- this piece even if some slots came back nil
					bld_cache[i] = bo;
					local e = { i = i };
					pcall(function() e.name = tostring(bo:name()); end);
					pcall(function()
						local p = bo:central_position();
						e.x = p:get_x(); e.y = p:get_y(); e.z = p:get_z();
					end);
					pcall(function() e.health = bo:health(); end);
					pcall(function() e.garr = bo:is_garrisoned() and true or false; end);
					pcall(function() e.cap = bo:capacity(); end);
					pcall(function() e.owner = bo:alliance_owner_id(); end);
					-- damage state (08-01, same blind-pcall class -- both
					-- getters are on the building object's known list)
					pcall(function() e.fire = bo:is_on_fire() and true or false; end);
					pcall(function() e.dead = bo:is_destroyed() and true or false; end);
					bld_rows[#bld_rows + 1] = e;
				end;
			end;
			bld_pos = stop;
			json.write(BLD, { total = total, scanned = bld_pos,
				kept = #bld_rows, rows = bld_rows, clock = os.clock(),
				battle_id = rawget(_G, "aai_battle_id") });
			return "v1 " .. from .. "-" .. stop .. "/" .. tostring(scan)
				.. " kept " .. tostring(#bld_rows);
		end;
		if bld_pos >= total then	-- done (or empty): restart fresh
			bld_pos = 0; bld_cache = {}; bld_rows = {};
		end;
		local from = bld_pos + 1;
		local stop = bld_pos + chunk;
		if stop > total then stop = total; end;
		log("bld chunk " .. from .. "-" .. stop .. " of " .. total
			.. (mode ~= "full" and (" " .. mode) or ""));
		local cold_at = nil;
		local cold_mt = nil;
		for i = from, stop do
			local bo = list:item(i);
			if bo ~= nil and type(bo) ~= "function" then
				-- VM-only metatable census BEFORE any engine call on the
				-- entry: warm entries measured mt_keys=10 (07-31 ladder);
				-- a different value on a cold entry = the touch-free cold
				-- detector we are hunting
				local mk = bis_mtinfo(bo);
				local ok_n, nv = pcall(function() return bo:name(); end);
				if ok_n and type(nv) == "function" then
					-- COLD: bail before touching anything else; the cursor
					-- stays here so the next click retries this entry
					cold_at = i;
					cold_mt = mk;
					log("bld COLD at i=" .. i .. " mt_keys=" .. mk
						.. " (warm reads 10) -- chunk aborted");
					break;
				end;
				local nm = ok_n and tostring(nv or "") or "";
				if i % 50 == 0 then
					log("bld i=" .. i .. " " .. nm);
				end;
				-- corpses + ground tiles drown the buildings (121 of the
				-- first 300 on 07-31's scan were dead bodies) -- skip them
				if not (nm:find("dead_body", 1, true)
						or nm:find("global_ground", 1, true)) then
					bld_cache[#bld_cache + 1] = bo;
					local e = { i = #bld_cache, name = nm, mt = mk };
					if mode ~= "names" then
						-- OBJECT-RETURNING call -- the kill suspect; the
						-- names mode exists to never reach this line
						pcall(function()
							local p = bo:central_position();
							if p ~= nil and type(p) ~= "function" then
								e.x = p:get_x(); e.y = p:get_y(); e.z = p:get_z();
							end;
						end);
					end;
					if mode == "full" then
						pcall(function()
							local v = bo:health();
							if type(v) ~= "function" then e.health = v; end;
						end);
						pcall(function()
							local v = bo:is_garrisoned();
							if type(v) ~= "function" then e.garr = (v == true); end;
						end);
						pcall(function()
							local v = bo:capacity();
							if type(v) ~= "function" then e.cap = v; end;
						end);
						pcall(function()
							local v = bo:alliance_owner_id();
							if type(v) ~= "function" then e.owner = v; end;
						end);
					end;
					bld_rows[#bld_rows + 1] = e;
				end;
				bld_pos = i;
			else
				bld_pos = i;
			end;
		end;
		json.write(BLD, { total = total, scanned = bld_pos, kept = #bld_rows,
			cold_at = cold_at, cold_mt = cold_mt, clock = os.clock(),
			battle_id = rawget(_G, "aai_battle_id"), rows = bld_rows });
		if cold_at ~= nil then
			return "COLD at " .. cold_at .. "/" .. total
				.. " mt_keys=" .. tostring(cold_mt)
				.. " -- CHECK THE FEED (warmth is a LIVE state: entries"
				.. " revert cold; no timing rule is safe)";
		end;
		return tostring(bld_pos) .. "/" .. tostring(total)
			.. " kept " .. tostring(#bld_rows)
			.. (mode ~= "full" and (" (" .. mode .. ")") or "");
	end,
	-- BISECT LADDER (07-31): ONE engine touch per click, in the exact call
	-- order of the bld scan, to pin the single lethal call. The pump polls
	-- the click mailbox, so the first step that kills the tick dispatch is
	-- simply the LAST ONE ACKED -- the ladder self-terminates on the
	-- killer. Steps marked VM-only never enter engine code (metatable
	-- census): if a cold slot's metatable differs from a warm one's, that
	-- is a touch-free cold detector -- the only possible Lua-side rescue.
	-- Late steps test the bm-level list accessor and the bulk walks. The
	-- step log line lands BEFORE the call so even a hard crash names its
	-- killer. Arg = jump to step N (prereq handles are re-acquired
	-- lazily, which re-runs their touches); no arg = auto-advance. State
	-- resets each battle. 07-31 late CONTROL RUN (vt 3200, ~5 min in):
	-- all steps passed, registry fully warm, mt_keys=10 both entries --
	-- the decisive run is at conflict START in a cold registry.
	bldstep = function(a)
		local step = tonumber(a and a[1]) or bis_next;
		local vt = tonumber(rawget(_G, "aai_tick_count")) or -1;
		local function need_list()
			if bis_list == nil then
				bis_list = battle:buildings();
			end;
			return bis_list;
		end;
		local function need_b1()
			if bis_b1 == nil then
				bis_b1 = need_list():item(1);
			end;
			return bis_b1;
		end;
		local function need_b2()
			if bis_b2 == nil then
				bis_b2 = need_list():item(2);
			end;
			return bis_b2;
		end;
		local S = {
			{ "acquire battle:buildings()", function()
				bis_list = battle:buildings();
				return "type=" .. type(bis_list);
			end },
			{ "list:count()", function()
				return "count=" .. tostring(need_list():count());
			end },
			{ "item(1), no calls on it", function()
				bis_b1 = need_list():item(1);
				return "type=" .. type(bis_b1);
			end },
			{ "VM-only: metatable(item1)", function()
				return "mt_keys=" .. bis_mtinfo(need_b1());
			end },
			{ "item(1):name()", function()
				local v = need_b1():name();
				return type(v) .. ":" .. tostring(v);
			end },
			{ "item(1):central_position()", function()
				local v = need_b1():central_position();
				return "type=" .. type(v);
			end },
			{ "item(2), no calls on it", function()
				bis_b2 = need_list():item(2);
				return "type=" .. type(bis_b2);
			end },
			{ "VM-only: metatable(item2) vs item1", function()
				local b1 = need_b1();
				local b2 = need_b2();
				local same = false;
				pcall(function()
					same = rawequal(debug.getmetatable(b1),
						debug.getmetatable(b2));
				end);
				return "mt_keys(1)=" .. bis_mtinfo(b1)
					.. " mt_keys(2)=" .. bis_mtinfo(b2)
					.. " same_mt=" .. tostring(same);
			end },
			{ "item(2):name() -- the known cold call", function()
				local v = need_b2():name();
				return type(v) .. ":" .. tostring(v);
			end },
			{ "alt accessor: bm:buildings() + count", function()
				-- get_building_near was a PHANTOM (nil on bm, absent from
				-- the whole reflect surface -- acked ERR on the 07-31
				-- control run). Replacement question: does the OTHER list
				-- accessor (bm-level, the one old write_geometry crashed
				-- on) behave like battle:buildings()?
				local bl = bm:buildings();
				local cnt = "?";
				pcall(function() cnt = tostring(bl:count()); end);
				return "type=" .. type(bl) .. " count=" .. cnt;
			end },
			{ "walk item(1..250), no method calls", function()
				local list = need_list();
				local top = list:count();
				if top > 250 then
					top = 250;
				end;
				local ud = 0;
				for i = 1, top do
					if type(list:item(i)) == "userdata" then
						ud = ud + 1;
					end;
				end;
				return "userdata=" .. ud .. "/" .. top;
			end },
			{ "walk name(1..250) pcall'd, warm/cold census", function()
				local list = need_list();
				local top = list:count();
				if top > 250 then
					top = 250;
				end;
				local warm, cold = 0, 0;
				for i = 1, top do
					local ok, v = pcall(function()
						return list:item(i):name();
					end);
					if ok and type(v) == "string" then
						warm = warm + 1;
					else
						cold = cold + 1;
					end;
				end;
				return "warm=" .. warm .. " cold=" .. cold .. "/" .. top;
			end },
		};
		local s = S[step];
		if not s then
			bis_next = 1;
			error("no step " .. tostring(step)
				.. " (ladder is 1.." .. #S .. "; cursor reset)");
		end;
		bis_next = step + 1;	-- advance BEFORE running: an ERR ack still moves on
		log("BISECT step " .. step .. " (" .. s[1] .. ") vt=" .. vt);
		local val = s[2]();
		return "step " .. step .. "/" .. #S .. " [" .. s[1] .. "] -> "
			.. tostring(val) .. " vt=" .. vt;
	end,
	-- (bdestroyi removed 2026-08-01 -- building:destroy() is an instant
	-- scripted demolish = cheat-class battle-level write, out of scope;
	-- the ORDERED attack_building unit verb (battk) stays)
	-- SIEGE ENGINES probe (07-31, Theo's order): enumerate the assault
	-- vehicles (rams/towers) into data/aai_aeq.json for the cockpit --
	-- positions on the map + "claimed" = crewed/equipped by a unit. The
	-- vehicle object's method surface was NEVER dumped (no battle had one
	-- until tonight's count=4), so this probe SELF-DISCOVERS: VM-only
	-- metatable census first, then it calls ONLY getters that exist,
	-- each pcall'd + unwrap-guarded. Vehicle 1's full method list lands
	-- in the JSON (`methods`) for RE. Handles cache into aeq_cache for
	-- the occupy/interact verbs. Ack = count + how many gave positions +
	-- how many read claimed.
	aeq = function()
		local eq = battle:assault_equipment();
		local n2 = eq:vehicle_count();
		aeq_cache = {};
		local cnt = (type(n2) == "number") and n2 or 0;
		if cnt > 40 then cnt = 40; end;
		local function uread(o, m)
			local ok, v = pcall(function() return o[m](o); end);
			if not ok then
				return nil;
			end;
			if type(v) == "function" then
				local ok2, v2 = pcall(v);
				if not ok2 then
					return nil;
				end;
				v = v2;
			end;
			return v;
		end;
		-- first existing getter (per the metatable census) returning the
		-- wanted type
		local function first(o, has, names, want)
			for _, m in ipairs(names) do
				if has[m] then
					local v = uread(o, m);
					if type(v) == want then
						return v;
					end;
				end;
			end;
			return nil;
		end;
		local rows = {};
		local methods = {};
		for i = 1, cnt do
			local ok_v, vo = pcall(function() return eq:vehicle_item(i); end);
			if ok_v and type(vo) == "userdata" then
				aeq_cache[#aeq_cache + 1] = vo;
				local e = { i = #aeq_cache };
				local has = {};
				pcall(function()
					local mt = debug.getmetatable(vo);
					local ix = (type(mt) == "table")
						and rawget(mt, "__index") or nil;
					for _, t in ipairs({ mt, ix }) do
						if type(t) == "table" then
							for k, _ in pairs(t) do
								has[tostring(k)] = true;
							end;
						end;
					end;
				end);
				if #methods == 0 then
					for k, _ in pairs(has) do
						methods[#methods + 1] = k;
					end;
					table.sort(methods);
				end;
				e.name = first(vo, has,
					{ "name", "vehicle_key", "key", "type" }, "string");
				local p = first(vo, has,
					{ "position", "central_position" }, "userdata");
				if p ~= nil then
					pcall(function()
						e.x = p:get_x(); e.y = p:get_y(); e.z = p:get_z();
					end);
				end;
				e.hp = first(vo, has, { "health", "hitpoints" }, "number");
				e.owner = first(vo, has, { "alliance_owner_id",
					"alliance_id", "owner_id", "owner" }, "number");
				local cl = first(vo, has, { "is_occupied", "is_manned",
					"is_crewed", "is_in_use", "has_crew",
					"is_garrisoned" }, "boolean");
				if cl ~= nil then
					e.claimed = cl;
				end;
				-- unit-link getters: WHO claimed it, when the engine
				-- exposes the crew unit
				local cu = first(vo, has, { "occupying_unit",
					"owning_unit", "unit" }, "userdata");
				if cu ~= nil then
					local un = uread(cu, "name");
					local ut = uread(cu, "type");
					e.by = tostring(un or "")
						.. ((ut ~= nil) and (" " .. tostring(ut)) or "");
					if e.claimed == nil then
						e.claimed = true;
					end;
				end;
				rows[#rows + 1] = e;
			end;
		end;
		json.write(AEQ, { count = n2, rows = rows, methods = methods,
			clock = os.clock(), battle_id = rawget(_G, "aai_battle_id") });
		local npos, ncl = 0, 0;
		for _, e in ipairs(rows) do
			if e.x ~= nil then
				npos = npos + 1;
			end;
			if e.claimed == true then
				ncl = ncl + 1;
			end;
		end;
		return tostring(n2) .. " engines, pos " .. npos
			.. ", claimed " .. ncl;
	end,
	-- victory-point getter re-probe: acks what each candidate getter returns
	-- (they probed nil pre-port; re-check live on THIS install)
	vp = function()
		local out = {};
		for _, nm in ipairs({ "fort_plazas", "capture_locations",
				"capture_points", "victory_locations", "plazas" }) do
			local got = "nil";
			pcall(function()
				local l = battle[nm](battle);
				if l ~= nil then
					got = type(l);
					pcall(function() got = got .. ":n=" .. tostring(l:count()); end);
				end;
			end);
			out[#out + 1] = nm .. "=" .. got;
		end;
		return table.concat(out, " ");
	end,
	-- (ships / reinforcement probes removed 2026-08-01 -- Theo: "Dont need
	-- this." on all five reinforcement/ships read lines)
};

local VERBS = {
	take = function(e) bridge.take(e.uc); end,
	release = function(e) bridge.release(e.uc); end,
	halt = function(e) bridge.halt(e.uc); end,
	move = function(e, a) bridge.move(e.uc, n(a[1]), n(a[2]), b(a[3])); end,
	form = function(e, a) bridge.form(e.uc, n(a[1]), n(a[2]), n(a[3]), n(a[4]), b(a[5])); end,
	apos = function(e, a) bridge.attack_pos(e.uc, n(a[1]), n(a[2]), b(a[3])); end,
	occupy = function(e, a) bridge.occupy_zone(e.uc, n(a[1]), n(a[2]), b(a[3])); end,
	withdraw = function(e, a) bridge.withdraw(e.uc, b(a[1])); end,
	rotate = function(e, a) e.uc:rotate(n(a[1])); end,
	stepf = function(e) e.uc:step_forward(); end,
	stepb = function(e) e.uc:step_backward(); end,
	faw = function(e, a) bridge.fire_at_will(e.uc, b(a[1])); end,
	mlee = function(e, a) bridge.melee(e.uc, b(a[1])); end,
	shot = function(e, a) bridge.shot_type(e.uc, a[1]); end,
	aunit = function(e, a) bridge.attack_unit_near(e.uc, n(a[1]), n(a[2])); end,
	beh = function(e, a) e.uc:change_behaviour_active(a[1], b(a[2])); end,
	-- (gform verb removed 2026-07-29: group formations are Theo's "useless
	-- tier" -- out of scope)
	winc = function(e) e.uc:increment_formation_width(); end,
	wdec = function(e) e.uc:decrement_formation_width(); end,
	abil = function(e, a) e.uc:perform_special_ability(a[1]); end,
	enabled = function(e, a) e.uc:change_enabled(b(a[1])); end,
	-- (2026-07-29 cull, Theo's order: teleport/aline/walkspeed/movespeed/
	-- morale/fatigue/killmen/ammof/kill/taunt/celebrate/highlight/hidecard/
	-- alwaysvis/invin/invis verbs removed -- no cheats, no cosmetics)
	-- siege verbs by bld/aeq cache index (run the bld / aeq probes first)
	battk = function(e, a)
		local bo = bld_cache[n(a[1])];
		if not bo then error("run bld first / bad index"); end;
		e.uc:attack_building(bo);
	end,
	climb = function(e, a)
		local bo = bld_cache[n(a[1])];
		if not bo then error("run bld first / bad index"); end;
		e.uc:climb_building(bo);
	end,
	leavebld = function(e) e.uc:leave_building(); end,
	defendbld = function(e, a)
		local bo = bld_cache[n(a[1])];
		if not bo then error("run bld first / bad index"); end;
		e.uc:defend_building(bo);
	end,
	usedep = function(e, a)
		local d = aeq_cache[n(a[1])];
		if not d then error("run aeq first / bad index"); end;
		e.uc:occupy_vehicle(d);
	end,
	usedep2 = function(e, a)
		local d = aeq_cache[n(a[1])];
		if not d then error("run aeq first / bad index"); end;
		e.uc:interact_with_deployable(d);
	end,
	deployr = function(e, a) e.unit:deploy_reinforcement(b(a[1])); end,
};

--------------------------------------------------------------------------
--	mailbox: "seq <n>" then "<verb> <key> [args...]" (server writes atomically)
--------------------------------------------------------------------------
local function read_order()
	local f = io.open(ORDER, "r");
	if not f then
		return nil;
	end;
	local l1 = f:read("*l") or "";
	local l2 = f:read("*l") or "";
	f:close();
	local seq = tonumber(string.match(l1, "^seq%s+(%d+)"));
	if not seq or seq <= last_seq or l2 == "" then
		return nil;
	end;
	return seq, l2;
end;

local function apply(seq, line)
	last_seq = seq;
	local parts = {};
	for w in string.gmatch(line, "%S+") do
		parts[#parts + 1] = w;
	end;
	local verb, key = parts[1], parts[2];
	-- global verbs (no unit key; args start right after the verb)
	local gfn = GLOBALS[verb or ""];
	if gfn then
		local gargs = {};
		for i = 2, #parts do
			gargs[#gargs + 1] = parts[i];
		end;
		local gok, gret = pcall(function() return gfn(gargs); end);
		if gok then
			ack(seq, line, true, nil, gret);
		else
			ack(seq, line, false, gret);
		end;
		return;
	end;
	local fn = VERBS[verb or ""];
	if not fn then
		ack(seq, line, false, "unknown verb");
		return;
	end;
	local e = squad(key or "");
	if not e then
		ack(seq, line, false, "no AI unit with key " .. tostring(key));
		return;
	end;
	local args = {};
	for i = 3, #parts do
		args[#args + 1] = parts[i];
	end;
	-- ownership: take before acting (not durable; harmless if repeated).
	-- take/release themselves skip the auto-take.
	if verb ~= "take" and verb ~= "release" then
		pcall(function() bridge.take(e.uc); end);
	end;
	ordered[key] = true;	-- freeze exempts every unit with a standing order
	local ok, ret = pcall(function() return fn(e, args); end);
	if ok then
		ack(seq, line, true, nil, ret);
	else
		ack(seq, line, false, ret);
	end;
end;

-- park the AI side: re-take everything each pump, halt on seizure or drift
local function freeze_tick()
	local cache = nil;
	pcall(function() cache = bridge.sync_squads(); end);
	if not cache then
		return;
	end;
	local newly = 0;
	for k, e in pairs(cache) do
		if not e.pre then
			pcall(function() bridge.take(e.uc); end);
			local moving = false;
			pcall(function() moving = e.unit:is_moving() and true or false; end);
			if (not seized[k]) or (moving and not ordered[k]) then
				pcall(function() bridge.halt(e.uc); end);
				if not seized[k] then
					newly = newly + 1;
				end;
				seized[k] = true;
			end;
		end;
	end;
	if newly > 0 then
		log("freeze: seized+halted " .. newly .. " AI units");
	end;
end;

local function pump()
	ticks = ticks + 1;
	if not armed() then
		return;
	end;
	-- drain captured command events (any phase -- deployment orders count too)
	local rows = rawget(_G, "aai_cmd_rows");
	if type(rows) == "table" and #rows > 0 then
		local moved = 0;
		while #rows > 0 and moved < 20 do
			local c = table.remove(rows, 1);
			cmd_seen = cmd_seen + 1;
			local nm = tostring(c.nm);
			cmd_names[nm] = (cmd_names[nm] or 0) + 1;
			local ex = tostring(c.extra or "");
			-- attribution capture (aai_attrib_on.txt, retested safe 07-30):
			-- the probe stored the event's raw unit userdata; THIS context
			-- may legally read it
			if c.u ~= nil then
				pcall(function()
					ex = ex .. " unit=" .. tostring(c.u:name()) ..
						"/" .. tostring(c.u:type());
				end);
			end;
			cmd_recent[#cmd_recent + 1] = { nm = nm, extra = ex };
			if #cmd_recent > 30 then
				table.remove(cmd_recent, 1);
			end;
			moved = moved + 1;
		end;
		pcall(function()
			json.write(CMDS, { n = cmd_seen, rows = cmd_recent,
				names = cmd_names });
		end);
	end;
	if cap_dirty then
		cap_dirty = false;
		pcall(function()
			json.write(CAPEV, { n = #cap_events, rows = cap_events });
		end);
	end;
	if freeze_on and phase == "conflict" then
		freeze_tick();
	end;
	local seq, line = nil, nil;
	pcall(function() seq, line = read_order(); end);
	if not seq then
		return;
	end;
	-- CONFLICT ONLY: engine writes from the pump during deployment froze
	-- the vanilla dispatch (2026-07-28). Hold with an explanatory ack.
	if phase ~= "conflict" then
		ack(seq, line, false, "engine writes are conflict-phase only (now: " ..
			tostring(phase) .. ")");
		last_seq = seq;
		return;
	end;
	apply(seq, line);
end;

--------------------------------------------------------------------------
function M.init(core)
	corel = core;
	bm = rawget(_G, "aai_bm");
	battle = bm and bm.battle or nil;
	bridge = rawget(_G, "aai_api");
	if not (bm and battle and bridge) then
		core.log("harness: no bridge (bootstrap instance) -- inactive");
		return;
	end;
	local bless = rawget(_G, "aai_bless");
	if bless then
		mkvec = bless(function(x, z) return v(x, z); end);
	end;
	-- clean slate on disk each battle (stale-layer bug 08-01: the cockpit
	-- was rendering the PREVIOUS battle's building/engine scans): empty
	-- files stamped with this battle's id, overwritten by real scans
	-- (reverted to the 15:53-proven form during the 08-01 evening crash revert)
	pcall(function()
		local bid = rawget(_G, "aai_battle_id");
		json.write(BLD, { total = 0, scanned = 0, kept = 0, rows = {},
			battle_id = bid, clock = os.clock() });
		json.write(AEQ, { count = 0, rows = {}, methods = {},
			battle_id = bid, clock = os.clock() });
	end);
	-- phase flags only (context law) -- mirrors publish/probe wiring
	local ev = rawget(_G, "events");
	if type(ev) == "table" then
		if ev.BattleDeploymentPhaseCommenced then
			ev.BattleDeploymentPhaseCommenced[#ev.BattleDeploymentPhaseCommenced + 1] =
				core.guarded("harness dep", function() phase = "deployment"; end);
		end;
		if ev.BattleConflictPhaseCommenced then
			ev.BattleConflictPhaseCommenced[#ev.BattleConflictPhaseCommenced + 1] =
				core.guarded("harness con", function() phase = "conflict"; end);
		end;
		if ev.BattleCompleted then
			ev.BattleCompleted[#ev.BattleCompleted + 1] =
				core.guarded("harness end", function() phase = "completed"; end);
		end;
		-- victory-point capture ticker: notify events, PURE-Lua handlers only
		-- (context law). 07-29 finding: a capture fired NEITHER name -- log
		-- which keys pre-exist (a key we create ourselves may never be
		-- dispatched by the engine at all).
		local pre = {};
		for _, nm in ipairs({
				"BattleFortPlazaCaptureCommenced",
				"BattleFortPlazaCaptureCompleted",
				"BattleFortPlazaCaptureAborted" }) do
			if ev[nm] ~= nil then pre[#pre + 1] = nm; end;
			ev[nm] = ev[nm] or {};
			local held = nm;
			ev[nm][#ev[nm] + 1] = core.guarded("harness cap", function()
				cap_events[#cap_events + 1] = { nm = held, clock = os.clock() };
				if #cap_events > 40 then table.remove(cap_events, 1); end;
				cap_dirty = true;
			end);
		end;
		log("capev keys pre-existing: " ..
			(#pre > 0 and table.concat(pre, ",") or "NONE"));
	end;
	rawset(_G, "aai_harness_tick", core.guarded("harness pump", pump));
	core.log("harness: hooked into publish pump, mailbox " .. ORDER ..
		" gate=" .. FLAG);
end;

return M;
