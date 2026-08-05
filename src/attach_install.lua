-------------------------------------------------------------------------
--	ATTILA-AI attach installer (branch custom-battles, Door B Route A).
--	THE ONE WRITE in this branch. Calls native aai_attach_arm, which copies
--	our chunk path into the engine's own std::string at BATTLE+0x64128 using
--	the engine's own copy-constructor; the engine's attach gate then does
--	everything else itself. See reference/CUSTOM_BATTLES.md.
--
--	ARMING IS STICKY (run 6): the lever data/aai_attach_arm.txt persists
--	across battles, so Theo arms once and every custom battle attaches.
--	Safety moved from "one-shot" to an explicit CRASH BREAKER:
--	  * data/aai_attach_inflight.txt is written just before the native arm
--	    and deleted by the chunk KERNEL once bring-up completes.
--	  * a marker still present at the NEXT battle load means the previous
--	    armed battle died inside the attach window -> disarm (delete the
--	    lever), write data/aai_attach_tripped.txt, and hold until re-armed
--	    from the cockpit. A crash can never repeat itself.
--	  * the native arm reports its outcome in data/aai_arm_status.txt
--	    ("ARMED*" / "REFUSE*"): on a refusal (campaign battle, replay,
--	    stale object) no attach is coming, so the marker is dropped --
--	    refusals must never trip the breaker.
-------------------------------------------------------------------------

local M = {};

local DLL = "data\\aai_native.dll";
local CHUNK = "data/aai/aai_attach.lua";
local LEVER = "data/aai_attach_arm.txt";
local INFLIGHT = "data/aai_attach_inflight.txt";
local TRIPPED = "data/aai_attach_tripped.txt";
local STATUS = "data/aai_arm_status.txt";

local function exists(path)
	local f = io.open(path, "r");
	if f then
		f:close();
		return true;
	end;
	return false;
end;

local function read_all(path)
	local f = io.open(path, "r");
	if not f then
		return nil;
	end;
	local s = f:read("*a");
	f:close();
	return s;
end;

local function touch(path, text)
	local f = io.open(path, "w");
	if f then
		f:write(text or "");
		f:close();
	end;
end;

function M.init(core)
	if core.world ~= "battle" then
		return;			-- never from the frontend: there is no BATTLE_ENV
	end;
	if not exists(LEVER) then
		-- say so: a battle that loads vanilla because nobody armed looks
		-- identical to a broken attach unless this line is in the log
		core.log("attach_install: lever absent -- not arming (cockpit arm "
			.. "button or data/aai_attach_arm.txt)");
		return;
	end;

	-- CRASH BREAKER: a surviving marker = the last armed battle never
	-- finished bring-up. Disarm and hold; re-arming is a cockpit click.
	if exists(INFLIGHT) then
		pcall(os.remove, LEVER);
		pcall(os.remove, INFLIGHT);
		touch(TRIPPED, "attach tripped: an armed battle died between arming "
			.. "and bring-up\n");
		core.log("ATTACH TRIPPED: the previous armed battle crashed inside "
			.. "the attach window -- DISARMED. Re-arm from the cockpit.");
		return;
	end;

	-- VFS pre-flight (precondition P6) -- NOT optional. The attach loader
	-- is the only one of its three sibling VFS readers that does not check
	-- stream validity, so an unresolvable path is a plausible fault on the
	-- first tick rather than a harmless miss. loadfile compiles only.
	local fn, err = loadfile(CHUNK);
	if not fn then
		core.log("ATTACH-ARM ABORT: preflight failed for " .. CHUNK
			.. " -> " .. tostring(err) .. " (nothing written; lever kept)");
		return;
	end;

	local ok, arm, lderr = pcall(package.loadlib, DLL, "aai_attach_arm");
	if not ok or type(arm) ~= "function" then
		core.log("ATTACH-ARM unavailable: " .. tostring(lderr or arm)
			.. " (stale aai_native.dll? py native/build_native.py)");
		return;
	end;

	-- marker BEFORE the call: a crash anywhere past this line leaves it
	-- on disk for the breaker
	pcall(os.remove, STATUS);
	touch(INFLIGHT, "armed at " .. tostring(os.date and os.date() or "?") .. "\n");

	local ran, e = pcall(arm);
	if not ran then
		core.log("ATTACH-ARM ERROR: " .. tostring(e));
	end;

	local status = read_all(STATUS) or "";
	if string.find(status, "ARMED", 1, true) then
		core.log("ATTACH-ARM armed -- kernel bring-up clears the inflight marker");
		-- marker stays: only the chunk kernel may release the breaker
	else
		pcall(os.remove, INFLIGHT);	-- no attach initiated -> nothing to guard
		if status == "" then
			core.log("ATTACH-ARM: no status file (stale aai_native.dll? "
				.. "rebuild native) -- breaker idle this battle");
		else
			core.log("ATTACH-ARM refused: "
				.. string.gsub(status, "%s+$", "") .. " (normal outside "
				.. "custom battles; lever kept)");
		end;
	end;
end;

return M;
