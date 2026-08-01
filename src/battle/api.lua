-------------------------------------------------------------------------
--	THE unified battle API. One interface over three providers:
--	  (1) LUA    -- the game's script binding, via the battle_entry bridge
--	                closures (_G.aai_api) + the battle_manager tree (_G.aai_bm)
--	  (2) NATIVE -- in-process DLL reads/writes            (battle/native.lua)
--	  (3) DB     -- static per-type stat card + abilities  (battle/db.lua)
--
--	Two entry points, both pure (no timers, no file I/O -- the publish/control
--	drivers own those):
--	  M.read_state(opts) -> the full battle snapshot table (every unit both
--	    sides + camera + reinforcements + misc), each field routed to its
--	    provider. Faithful superset of the old state_json exporter (same keys,
--	    so the cockpit contract is preserved) + native fatigue.
--	  M.issue(entry, verb, args) -> dispatch ONE unit action to its provider.
--	    Un-reverse-engineered actions are present but return
--	    false,"unavailable: <what to look for>" (never a silent no-op).
--
--	M.FIELDS / M.ACTIONS are the catalog: the documented contract of what is
--	readable/writable, which provider serves it, and -- for the gaps -- the
--	exact native RE seed (`native_todo`). Mirrors reference/CAPABILITIES_PLAN.md.
--
--	Context law: read_state must run from the engine-timer pump context (as
--	publish does) -- the same context the old state_json ran in. Never call it
--	from an events.* handler.
-------------------------------------------------------------------------
local M = {};

local native = require "battle/native";
local db = require "battle/db";

local bm = nil;			-- _G.aai_bm         (battle_manager)
local battle = nil;		-- _G.aai_bm.battle  (the engine battle interface state_json read from)
local bridge = nil;		-- _G.aai_api        (engine-context closures)
local F = native.FIELD;

--------------------------------------------------------------------------
--	Read primitives (ported verbatim from the proven state_json exporter)
--------------------------------------------------------------------------

-- pcall + function-unwrap: an Attila method field can yield a bound getter
-- FUNCTION instead of the value; call it. Returns nil on any failure.
local function read(obj, method, ...)
	local args = { ... };
	local ok, value = pcall(function() return obj[method](obj, unpack(args)); end);
	if not ok then
		return nil;
	end;
	if type(value) == "function" then
		local ok2, value2 = pcall(value);
		if ok2 then
			return value2;
		end;
		return nil;
	end;
	return value;
end;

-- a list :count() can return a non-number (function/nil) before it is
-- populated (sieges at load); feeding that to `for i=1,n` throws and kills the
-- whole snapshot. Coerce to 0 -> skip this tick, retry next.
local function num(v)
	return (type(v) == "number") and v or 0;
end;

local function vec(v)
	if not v then
		return nil;
	end;
	return { x = read(v, "get_x"), y = read(v, "get_y"), z = read(v, "get_z") };
end;

--------------------------------------------------------------------------
--	Per-unit assembly: LUA fields (superset of state_json) + NATIVE fatigue
--------------------------------------------------------------------------
local function unit_entry(unit, ai_alliance)
	local entry = {
		name = tostring(read(unit, "name") or "?"),
		pos = vec(read(unit, "position")),
		bearing = read(unit, "bearing"),
		men = read(unit, "number_of_men_alive"),
		men0 = read(unit, "initial_number_of_men"),
		ammo = read(unit, "ammo_left"),
		ammo0 = read(unit, "starting_ammo"),
		width = read(unit, "width"),
		routing = read(unit, "is_routing") and true or false,
		shattered = read(unit, "is_shattered") and true or false,
		cavalry = read(unit, "is_cavalry") and true or false,
		arty = read(unit, "is_artillery") and true or false,
		moving = read(unit, "is_moving") and true or false,
		idle = read(unit, "is_idle") and true or false,
		fast = read(unit, "is_moving_fast") and true or false,
		under_fire = read(unit, "is_under_missile_attack") and true or false,
		faw = read(unit, "is_behaviour_active", "fire_at_will"),
		loose = read(unit, "is_behaviour_active", "change_formation_spacing"),
		ordered = vec(read(unit, "ordered_position")),
		range = read(unit, "missile_range"),		-- 0 = melee; >0 = ranged
		inf = read(unit, "is_infantry") and true or false,
	};
	-- naval paradigm (2026-07-28): the engine has no is_naval; has_ships() IS
	-- the current-embarkment check, is_dismounted_ships() = naval unit ashore
	if read(unit, "has_ships") then entry.naval = true; end;
	if read(unit, "is_dismounted_ships") then entry.dships = true; end;
	-- harness read-panel fields (all probe-verified T1, 2026-07-28)
	entry.kills = read(unit, "number_of_enemies_killed");
	entry.owidth = read(unit, "ordered_width");
	entry.hidden = read(unit, "is_hidden") and true or false;
	entry.valid_tgt = read(unit, "is_valid_target") and true or false;
	entry.garrisoned = read(unit, "is_currently_garrisoned") and true or false;
	entry.leaving = read(unit, "is_leaving_battle") and true or false;
	entry.officer = vec(read(unit, "position_of_officer"));
	-- the engine's own toggle vocabulary (binary parse table; the engine
	-- accepts any string, so names earn their slot there or by effect;
	-- hiding is passive -- is_hidden)
	local st = {};
	for _, b in ipairs({ "skirmish", "defend", "formed_attack", "unlimber",
			"dismount", "release_animals", "drop_siege_equipment",
			"abandon_artillery_engines", "board_ship",
			"naval_fire_at_will" }) do
		if read(unit, "is_behaviour_active", b) then
			st[#st + 1] = b;
		end;
	end;
	if #st > 0 then entry.stances = st; end;
	local cls = read(unit, "unit_class");
	if cls ~= nil then entry.cls = tostring(cls); end;
	local car = read(unit, "can_perform_special_ability");
	entry.has_ability = (car ~= nil) and true or false;
	if car == true then entry.can_ability = true; end;
	local ab = read(unit, "current_special_ability");
	if ab ~= nil and tostring(ab) ~= "none" then entry.ability = tostring(ab); end;
	if ai_alliance ~= nil then
		local vz = read(unit, "is_visible_to_alliance", ai_alliance);
		if vz ~= nil then entry.vis = vz and true or false; end;
	end;
	local ty = read(unit, "type");
	if ty ~= nil then entry.type = tostring(ty); end;
	-- NATIVE (#2): fatigue is Lua-blind -- the DLL is the only source. Guarded
	-- offset (needs-confirm); appears when the DLL is loaded and the read lands.
	local fat = native.read(unit, F.fatigue);
	if fat ~= nil then entry.fatigue = fat; end;
	return entry;
end;

--------------------------------------------------------------------------
--	M.read_state(opts) -> full snapshot table (or nil to skip this frame)
--	opts = { phase = <string>, t = <battle seconds> }. Caller (publish) owns
--	phase/tick tracking + the file write + geometry fold-in + recorder.
--------------------------------------------------------------------------
function M.read_state(opts)
	opts = opts or {};
	local state = {
		kind = "battle",
		phase = opts.phase or "loading",
		t = opts.t or 0,
		player_alliance = read(battle, "local_alliance"),
	};
	-- engine battle clock: authoritative under modify_battle_speed, unlike our
	-- tick-derived t (Theo 07-29: t drifted visibly after a speed-up)
	local rem = read(battle, "remaining_conflict_time");
	if type(rem) == "number" then state.remaining = rem; end;
	local bover = read(battle, "is_battle_over");
	if bover ~= nil then state.over = bover and true or false; end;
	local pa = state.player_alliance or 1;

	local cam = read(battle, "camera");
	if cam then
		state.camera = {
			pos = vec(read(cam, "position")),
			target = vec(read(cam, "target")),
		};
	end;

	local alliances = {};
	local alist = read(battle, "alliances");
	local acount = num(alist and read(alist, "count"));
	local ai_idx = (pa == 1) and 2 or 1;
	local ai_alliance = read(alist, "item", ai_idx);
	for a = 1, acount do
		local armies_out = {};
		local alliance = read(alist, "item", a);
		local armies = alliance and read(alliance, "armies");
		local mcount = num(armies and read(armies, "count"));
		for m = 1, mcount do
			local units_out = {};
			local army = read(armies, "item", m);
			local units = army and read(army, "units");
			local ucount = num(units and read(units, "count"));
			for u = 1, ucount do
				local unit = read(units, "item", u);
				if unit then
					local e = unit_entry(unit, ai_alliance);
					e.i = u;
					if a ~= pa and bridge then
						local okk, k = pcall(bridge.unit_key, unit);
						if okk and k then e.key = k; end;
					end;
					units_out[#units_out + 1] = e;
				end;
			end;
			local army_out = { units = units_out, n = ucount };
			do
				local ca = read(army, "is_commander_alive");
				if ca ~= nil then army_out.commander_alive = ca and true or false; end;
				-- naval observability: ship + reinforcement-ship counts (0 on
				-- land battles; real numbers light up in a naval battle)
				local sh = read(army, "ships");
				if sh then army_out.ships = num(read(sh, "count")); end;
				local rsh = read(army, "get_reinforcement_ships");
				if rsh then army_out.reinf_ships = num(read(rsh, "count")); end;
				local runits = read(army, "get_reinforcement_units");
				if runits then
					local rc = num(read(runits, "count"));
					local rlist = {};
					for r = 1, rc do
						local ru = read(runits, "item", r);
						if ru then
							-- only count empty-named reserves (deployed
							-- reinforcements already appear in army:units())
							local rn = tostring(read(ru, "name") or "");
							if rn == "" then
								local rentry = {
									men = read(ru, "number_of_men_alive"),
									men0 = read(ru, "initial_number_of_men"),
								};
								local rt = read(ru, "type");
								if rt ~= nil then rentry.type = tostring(rt); end;
								local rcl = read(ru, "unit_class");
								if rcl ~= nil then rentry.cls = tostring(rcl); end;
								rlist[#rlist + 1] = rentry;
							end;
						end;
					end;
					if #rlist > 0 then army_out.reinf = rlist; end;
				end;
			end;
			armies_out[#armies_out + 1] = army_out;
		end;
		alliances[#alliances + 1] = { armies = armies_out };
	end;
	state.alliances = alliances;

	-- Guard transitional/teardown ticks where EVERY engine read returns nil
	-- (load->deploy handover, GC mid-tick): a real battle always has a
	-- local_alliance -> nil here means failed reads; skip the frame so the
	-- last good one stays on screen.
	if state.player_alliance == nil then
		return nil;
	end;
	return state;
end;

--------------------------------------------------------------------------
--	Battle-scoped helpers (not unit-scoped, so not routed through issue)
--------------------------------------------------------------------------

-- Enumerate live buildings (siege walls/gates/towers) for the geometry block.
-- Best-effort: names + positions always; health/on_fire when the engine
-- exposes them (unexercised in field battles -> confirm in a siege).
function M.buildings()
	if not bridge or not bridge.buildings then
		return nil;
	end;
	local ok, list = pcall(bridge.buildings);
	if ok then
		return list;
	end;
	return nil;
end;

-- Destroy the building nearest (x,z) -- the scripted stand-in for a ram/attack
-- on a wall/gate (rams have no uc command; vanilla scripts destroy directly).
function M.attack_building(x, z)
	if not bridge or not bridge.attack_building then
		return false, "no bridge";
	end;
	return pcall(bridge.attack_building, x, z);
end;

-- Force battle game-speed. DEAD in retail (see native.set_game_speed).
function M.set_game_speed(v)
	return native.set_game_speed(v);
end;

--------------------------------------------------------------------------
--	M.issue(entry, verb, args) -> ok, note. Dispatch ONE unit action.
--	entry = { unit=, uc= } (a squad-cache entry). Every engine call is
--	pcall-guarded so a driver gets a clean ok/false. Un-RE'd actions return
--	false + the native_todo string.
--------------------------------------------------------------------------
function M.issue(entry, verb, args)
	local a = M.ACTIONS[verb];
	if not a then
		return false, "unknown action: " .. tostring(verb);
	end;
	if a.avail == "native-todo" then
		return false, "unavailable: " .. (a.native_todo or "needs native RE");
	end;
	local uc = entry and entry.uc;
	if not uc then
		return false, "no controller for " .. tostring(verb);
	end;
	args = args or {};
	local ok, err = pcall(function()
		if verb == "move" then bridge.move(uc, args.x, args.z, args.run);
		elseif verb == "form" then bridge.form(uc, args.x, args.z, args.bearing, args.width, args.run);
		elseif verb == "apos" then bridge.attack_pos(uc, args.x, args.z, args.run);
		elseif verb == "aunit" then bridge.attack_unit_near(uc, args.x, args.z);
		elseif verb == "flee" then bridge.withdraw(uc, args.run);
		elseif verb == "halt" then bridge.halt(uc);
		elseif verb == "fire" then bridge.fire_at_will(uc, args.on);
		elseif verb == "melee" then bridge.melee(uc, args.on);
		elseif verb == "speed" then bridge.walk_speed(uc, args.mult);
		elseif verb == "shot_type" then bridge.shot_type(uc, args.name);
		elseif verb == "morale" then bridge.morale(uc, args.mode);
		elseif verb == "change_fatigue" then bridge.change_fatigue(uc, args.n);
		elseif verb == "kill" then bridge.kill(uc);
		elseif verb == "taunt" then bridge.taunt(uc);
		elseif verb == "ability" then bridge.ability(uc, args.idx);
		elseif verb == "occupy_zone" then bridge.occupy_zone(uc, args.x, args.z, args.run);
		elseif verb == "teleport" then bridge.teleport(uc, args.x, args.z, args.bearing, args.width);
		elseif verb == "take" then bridge.take(uc);
		elseif verb == "release" then bridge.release(uc);
		else error("unhandled verb " .. tostring(verb)); end;
	end);
	if not ok then
		return false, tostring(err);
	end;
	return true;
end;

--------------------------------------------------------------------------
--	CATALOG -- the documented contract (also the CAPABILITIES_PLAN mirror).
--	avail: "lua" (works) | "lua-attempt" (wired, unconfirmed) |
--	       "native" (DLL, wired) | "native-todo" (stub + RE seed) |
--	       "db" (static, client-joined by type) | "lua-probe" (siege-confirm)
--------------------------------------------------------------------------
M.FIELDS = {
	-- unit, LUA (emitted live per tick in read_state)
	{ name = "pos", provider = "lua", avail = "lua" },
	{ name = "bearing", provider = "lua", avail = "lua" },
	{ name = "men/men0", provider = "lua", avail = "lua" },
	{ name = "ammo/ammo0", provider = "lua", avail = "lua" },
	{ name = "width", provider = "lua", avail = "lua", note = "nil in this build -> UI estimates footprint" },
	{ name = "type/unit_class/name", provider = "lua", avail = "lua" },
	{ name = "routing/shattered", provider = "lua", avail = "lua" },
	{ name = "cavalry/arty/inf/naval", provider = "lua", avail = "lua" },
	{ name = "moving/idle/fast(run)", provider = "lua", avail = "lua" },
	{ name = "under_fire", provider = "lua", avail = "lua" },
	{ name = "faw(fire-at-will)/loose", provider = "lua", avail = "lua" },
	{ name = "ordered_position", provider = "lua", avail = "lua" },
	{ name = "missile_range", provider = "lua", avail = "lua" },
	{ name = "vis(fog to AI)", provider = "lua", avail = "lua" },
	{ name = "has/can/current ability", provider = "lua", avail = "lua" },
	{ name = "key(identity)", provider = "lua", avail = "lua" },
	-- unit, NATIVE
	{ name = "fatigue", provider = "native", avail = "native", note = "0x1cb4; needs live confirm (ramp test)" },
	{ name = "current_morale", provider = "native", avail = "native-todo",
	  native_todo = "float offset near fatigue 0x1cb4; drive a unit to waver/rout + differential-map. The UI morale bar reads this." },
	{ name = "experience", provider = "native", avail = "native-todo",
	  native_todo = "int offset (set at battle start from campaign); differential-map or read once." },
	{ name = "on_wall", provider = "native", avail = "native-todo",
	  native_todo = "flag offset; set while a unit occupies a wall segment (BattleUnitUsingWall is notify-only)." },
	{ name = "ship_damage/fire_damage", provider = "native", avail = "native-todo",
	  native_todo = "naval struct offsets (like building health); differential-map in a naval battle." },
	{ name = "soldiers[] (per-man pos)", provider = "native", avail = "native-todo",
	  native_todo = "soldier-entity array on the unit struct: find array pointer + per-soldier stride; iterate for individual positions." },
	-- unit, DB (static, delivered to the cockpit via /unitstats, joined by type)
	{ name = "stat card (armour/melee_attack/melee_defence/charge_bonus/...)", provider = "db", avail = "db" },
	{ name = "abilities[] (roster by name)", provider = "db", avail = "db" },
	-- battlefield
	{ name = "buildings (walls/gates/towers pos+name)", provider = "lua", avail = "lua-probe",
	  note = "bm:buildings(); empty in field battles -> confirm enumeration + health in a siege" },
	{ name = "building/wall health, on_fire", provider = "lua", avail = "lua-probe",
	  native_todo = "probe building:health()/is_on_fire()/is_destroyed() in a SIEGE (unexercised); native offset fallback." },
	{ name = "terrain elevation / land-vs-water", provider = "native", avail = "native-todo",
	  native_todo = "stopgap: sample vector:get_y() on a grid (Lua). Full: native heightmap pointer + water-plane test." },
	{ name = "walkable / pathfinding grid", provider = "native", avail = "native-todo",
	  native_todo = "native navmesh/path grid structure; no Lua getter." },
	{ name = "victory-point regions + capture status", provider = "native", avail = "native-todo",
	  native_todo = "capture-point manager struct; Lua getters probed nil. Live capture via BattleFortPlazaCaptureCommenced (notify) + native offset." },
	{ name = "naval disembark points", provider = "native", avail = "native-todo",
	  native_todo = "native map metadata; no Lua getter." },
	-- misc (LUA)
	{ name = "phase / timer", provider = "lua", avail = "lua" },
	{ name = "reinforcements (amount/type)", provider = "lua", avail = "lua" },
};

M.ACTIONS = {
	-- movement / position (LUA, confirmed)
	move = { provider = "lua", avail = "lua", desc = "goto_location" },
	form = { provider = "lua", avail = "lua", desc = "goto_location_angle_width (pos+bearing+frontage)" },
	teleport = { provider = "lua", avail = "lua", desc = "teleport_to_location (instant deploy)" },
	occupy_zone = { provider = "lua", avail = "lua", desc = "occupy_zone -- move+hold; engine auto-climbs walls (mount)" },
	halt = { provider = "lua", avail = "lua", desc = "halt" },
	flee = { provider = "lua", avail = "lua", desc = "withdraw" },
	speed = { provider = "lua", avail = "lua", desc = "change_current_walk_speed (run = the move run-flag)" },
	-- attack (LUA, confirmed)
	apos = { provider = "lua", avail = "lua", desc = "attack_location (attack ground / artillery bombard)" },
	aunit = { provider = "lua", avail = "lua", desc = "attack_unit nearest player unit" },
	fire = { provider = "lua", avail = "lua", desc = "fire_at_will on/off" },
	melee = { provider = "lua", avail = "lua", desc = "melee lock on/off (skirmish inverse)" },
	shot_type = { provider = "lua", avail = "lua", desc = "change_shot_type (ammo type: fire shot etc.)" },
	-- morale / one-shots (LUA, confirmed)
	morale = { provider = "lua", avail = "lua", desc = "morale_behavior_rout/fearless/default" },
	change_fatigue = { provider = "lua", avail = "lua", desc = "change_fatigue_amount" },
	taunt = { provider = "lua", avail = "lua", desc = "start_taunting" },
	kill = { provider = "lua", avail = "lua", desc = "kill" },
	take = { provider = "lua", avail = "lua", desc = "take_control (control policy uses per-tick)" },
	release = { provider = "lua", avail = "lua", desc = "release_control" },
	-- abilities (LUA attempt; native fallback if the binding is inert)
	ability = { provider = "lua", avail = "lua-attempt", desc = "perform_special_ability(idx)",
		native_todo = "if perform_special_ability is nil/no-op on AI units, find the engine ability-activation fn (seed: BattleUnitUsingSpecialAbility handler / the ability system reading the unit ability list) and call with (unit_P, ability_id). Ability ids: db abilities[] roster (form_*, frenzy, chant, ...)." },
	-- formations (STUB -- native RE)
	set_formation = { provider = "native", avail = "native-todo", desc = "shield_wall/testudo/phalanx/loose/wedge/...",
		native_todo = "is_behaviour_active(name) READS formation state but uc has NO setter. Find the set-behaviour/formation writer fn; call with (unit_P, formation_id). Candidate ids in db abilities[]: form_hoplite_phalanx, form_pike_square, form_flying_wedge, form_diamond." },
	-- naval (STUB -- native RE)
	naval_ram = { provider = "native", avail = "native-todo", desc = "ram target ship",
		native_todo = "no Lua command (BattleBoardingActionCommenced is notify-only). Find the ram order fn (seed: att_ramming_speed ability in db). Call with (ship_P, target_P)." },
	naval_board = { provider = "native", avail = "native-todo", desc = "board target ship",
		native_todo = "no Lua command. Find the boarding order fn; call with (ship_P, target_P)." },
	naval_disembark = { provider = "native", avail = "native-todo", desc = "disembark at point",
		native_todo = "no Lua command. Find the disembark order fn; call with (unit_P, x, z)." },
};

--------------------------------------------------------------------------
function M.init(core)
	bm = rawget(_G, "aai_bm");
	battle = bm and bm.battle or nil;
	bridge = rawget(_G, "aai_api");
	native.init(core);
	db.init(core);
	if core then
		if not battle or not bridge then
			core.log("api: no bridge (bootstrap instance) -- read_state/issue inactive");
		else
			core.log("api: unified battle API ready (providers: lua+native+db)");
		end;
	end;
	return (battle ~= nil and bridge ~= nil);
end;

function M.ready()
	return battle ~= nil and bridge ~= nil;
end;

return M;
