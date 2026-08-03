-------------------------------------------------------------------------
--	ml/game/providers/native.lua -- T3/T4/T5: the in-process DLL door.
--
--	aai_native.dll (shared infra, built from native/) loaded via
--	package.loadlib, each export resolved by name, all calls pcall'd.
--	Inert-if-absent law: a missing/stale DLL or the aai_native_off.txt
--	lever must degrade every accessor to nil/false -- never break the
--	pipeline. The DLL ABI: aai_read_field(unit_userdata, field_id) ->
--	number|nil; aai_write_field(unit_userdata, field_id, value) -> 1|nil.
--	Fields are offset-map-gated (AAI_FIELDS in native/aai_native.c);
--	adding one = C row + M.FIELD id here + rebuild, no new export.
-------------------------------------------------------------------------

local M = {};

local DLL = "data\\aai_native.dll";

-- Mirror of AAI_FIELDS (id -> meaning). Confirmation state per NATIVE.md:
-- only men is live-CONFIRMED; the rest are needs-confirm offsets.
M.FIELD = {
	men = 1,			-- 0x0044 i32 CONFIRMED (== number_of_men_alive)
	fatigue = 2,		-- 0x1cb4 f32 needs-confirm (feat idx 52 source)
	ammo = 3,			-- 0x20cc f32 needs-confirm
	ammo_max = 4,		-- 0x20d0 i32 needs-confirm
	ammo_frac = 5,		-- 0x20d8 f32 needs-confirm
	rout_state = 6,		-- 0x1f28 i32 needs-confirm (7 = routing)
	order_state = 7,	-- 0x1b5c i32 unverified (feat [70:84] source candidate)
	rout_counter = 8,	-- 0x212c i32 unverified
	-- morale VALUE float: not yet found (differential-mapper target);
	-- feat idx 51 stays zero until it lands.
};

function M.init(core)
	-- todo: honor aai_native_off.txt; pcall(package.loadlib, DLL, sym) per
	-- export (aai_read_field / aai_write_field); cache fns; M.ok = loaded
end;

M.ok = false;

function M.read(unit, field_id)
	return nil, "todo(T3 aai_read_field; nil when DLL absent or read fails)";
end;

function M.write(unit, field_id, value)
	return false, "todo(T4 aai_write_field; policy: never surfaced as a cheat)";
end;

return M;
