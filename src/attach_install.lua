-------------------------------------------------------------------------
--	ATTILA-AI attach installer (branch custom-battles, Door B Route A).
--	THE ONE WRITE in this branch. Calls native aai_attach_arm, which copies
--	our chunk path into the engine's own std::string at BATTLE+0x64128 using
--	the engine's own copy-constructor. The engine's attach gate (later in the
--	SAME ctor, same thread) then does everything else itself.
--
--	Runs in the BATTLE bootstrap world only -- that call site is the one
--	moment when the target string is constructed, unowned, and the gate has
--	not yet run. See reference/CUSTOM_BATTLES.md.
--
--	DEFAULT-OFF, lever data/aai_attach_arm.txt. The lever is deleted BEFORE
--	the native call, so if anything goes wrong the next launch is vanilla by
--	construction -- recovery never needs a rebuild, just don't re-arm.
-------------------------------------------------------------------------

local M = {};

local DLL = "data\\aai_native.dll";
local CHUNK = "data/aai/aai_attach.lua";

function M.init(core)
	if core.world ~= "battle" then
		return;			-- never from the frontend: there is no BATTLE_ENV
	end;
	local lever = io.open("data/aai_attach_arm.txt", "r");
	if not lever then
		return;
	end;
	lever:close();

	-- DISARM FIRST. If the write or the subsequent attach crashes the game,
	-- the lever is already gone and the next launch behaves like vanilla.
	pcall(os.remove, "data/aai_attach_arm.txt");

	-- VFS pre-flight (installer precondition P6) -- NOT optional. The attach
	-- loader is the only one of its three sibling VFS readers that does not
	-- check stream validity, so an unresolvable path is a plausible fault on
	-- the first tick rather than a harmless miss. loadfile compiles only.
	local fn, err = loadfile(CHUNK);
	if not fn then
		core.log("ATTACH-ARM ABORT: preflight failed for " .. CHUNK
			.. " -> " .. tostring(err) .. " (nothing written)");
		return;
	end;

	local ok, arm, lderr = pcall(package.loadlib, DLL, "aai_attach_arm");
	if not ok or type(arm) ~= "function" then
		core.log("ATTACH-ARM unavailable: " .. tostring(lderr or arm)
			.. " (stale aai_native.dll? py native/build_native.py)");
		return;
	end;

	local ran, e = pcall(arm);
	if ran then
		core.log("ATTACH-ARM fired -- see data/aai_attach_install.txt;"
			.. " if it attached, data/aai_attach_chunk.txt appears");
	else
		core.log("ATTACH-ARM ERROR: " .. tostring(e));
	end;
end;

return M;
