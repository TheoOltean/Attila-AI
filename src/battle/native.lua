-------------------------------------------------------------------------
--	Native provider (#2 of 3). Loads the in-process DLL data/aai_native.dll
--	via package.loadlib and exposes the generic field read/write plus the
--	differential-mapper hooks. Degrades to fully inert if the DLL is absent
--	or an export is missing -- every accessor is pcall-guarded and returns
--	nil/false rather than throwing, so a stale/missing DLL never breaks the
--	live feed or the control loop.
--
--	Field ids MIRROR the AAI_FIELDS table in native/aai_native.c (id ->
--	offset,type). Only men(0x44) is exactly confirmed live; fatigue/ammo/
--	rout are best-known offsets -- the DLL guards every read with
--	IsBadReadPtr, so a wrong offset yields nil, never a crash.
--
--	Offsets + the payload->W->P chain: ghidra/findings/unit_field_map.md,
--	native/NATIVE.md.
-------------------------------------------------------------------------
local M = {};

local DLL = "data\\aai_native.dll";	-- game-CWD-relative (package.loadlib path)

-- field-id contract; keep in lockstep with AAI_FIELDS in aai_native.c
M.FIELD = {
	men          = 1,	-- int   CONFIRMED (== unit:number_of_men_alive())
	fatigue      = 2,	-- float needs-confirm  (Lua-blind: the native prize)
	ammo         = 3,	-- float needs-confirm
	ammo_max     = 4,	-- int
	ammo_frac    = 5,	-- float
	rout_state   = 6,	-- int   needs-confirm  (7 = routing)
	order_state  = 7,	-- int
	rout_counter = 8,	-- int
};

local read_field_fn     = nil;
local write_field_fn    = nil;
local set_speed_fn      = nil;
local snapshot_fn       = nil;
local snapshot_reset_fn = nil;
M.ok = false;

local function resolve(sym)
	local ok, fn = pcall(package.loadlib, DLL, sym);
	if ok and type(fn) == "function" then
		return fn;
	end;
	return nil;
end;

function M.init(core)
	-- proof-of-life: luaopen_aai only appends a pid line to a file, no stack use
	local open = resolve("luaopen_aai");
	if open then
		pcall(open);
	end;
	read_field_fn     = resolve("aai_read_field");
	write_field_fn    = resolve("aai_write_field");
	set_speed_fn      = resolve("aai_set_game_speed");
	snapshot_fn       = resolve("aai_snapshot");
	snapshot_reset_fn = resolve("aai_snapshot_reset");
	M.ok = (read_field_fn ~= nil);
	if core then
		if M.ok then
			core.log("native: aai_native.dll loaded -- read_field ready (write=" ..
				tostring(write_field_fn ~= nil) .. ")");
		else
			core.log("native: aai_native.dll UNAVAILABLE (missing/stale or no " ..
				"aai_read_field export) -- native fields inert, Lua+DB still work");
		end;
	end;
	return M.ok;
end;

-- read one numeric field off a live unit userdata; number or nil.
function M.read(unit, field_id)
	if not read_field_fn or unit == nil or field_id == nil then
		return nil;
	end;
	local ok, v = pcall(read_field_fn, unit, field_id);
	if ok and type(v) == "number" then
		return v;
	end;
	return nil;
end;

-- write one numeric field; returns true on a landed (guarded) store.
function M.write(unit, field_id, value)
	if not write_field_fn or unit == nil or field_id == nil then
		return false;
	end;
	local ok, v = pcall(write_field_fn, unit, field_id, value);
	return ok and v ~= nil;
end;

-- CONFIG WRITE: force battle game-speed. NOTE: DEAD in retail -- the write
-- lands at the right cell (readback confirms) but nothing in the shipped
-- engine reads BATTLE_OVERRIDE_GAME_SPEED. Kept for the record / future builds.
function M.set_game_speed(v)
	if not set_speed_fn then
		return false;
	end;
	return pcall(set_speed_fn, v);
end;

-- differential-mapper hooks (used only by the opt-in research module)
function M.snapshot(unit, key, tick)
	if snapshot_fn then
		pcall(snapshot_fn, unit, key, tick);
	end;
end;

function M.snapshot_reset()
	if snapshot_reset_fn then
		pcall(snapshot_reset_fn);
	end;
end;

return M;
