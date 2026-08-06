-------------------------------------------------------------------------
--	ATTILA-AI attach probe (branch custom-battles, Door B rung 3).
--	Calls the native READ-ONLY probe aai_attach_probe, which locates the
--	BATTLE object by identity and reports the state of the script-attach
--	gate (reference/CUSTOM_BATTLES.md). Nothing is written to the engine.
--
--	Runs in the BATTLE bootstrap world, which the engine constructs and
--	executes INSIDE the BATTLE_ENV constructor -- after the battle-setup
--	copy but BEFORE the attach gate. That is exactly the moment the
--	discriminator (B+0x64128, the <battle_script> path) is populated and
--	the interface slot is still NULL, so:
--	    SCENARIO battle -> path NON-EMPTY
--	    CUSTOM   battle -> path EMPTY   (the hypothesis under test)
--	Also listed by the frontend orchestrator as a negative control: with no
--	battle in existence it must find no BATTLE_ENV at all.
--
--	Report: data/aai_attach_probe.txt.  DEFAULT-OFF: gates on
--	data/aai_attach_probe.txt being ABSENT is NOT the trigger -- the lever
--	file is data/aai_attach_on.txt (kept distinct from the report file).
-------------------------------------------------------------------------

local M = {};

local DLL = "data\\aai_native.dll";

function M.init(core)
	local lever = io.open("data/aai_attach_on.txt", "r");
	if not lever then
		return;
	end;
	lever:close();

	-- package.loadlib does NOT raise: it returns nil + message + kind, so the
	-- diagnostic is the SECOND return, not the pcall error.
	local ok, fn, lderr = pcall(package.loadlib, DLL, "aai_attach_probe");
	if not ok or type(fn) ~= "function" then
		core.log("ATTACH-PROBE unavailable: " .. tostring(lderr or fn)
			.. " (stale aai_native.dll? rebuild with py native/build_native.py)");
		return;
	end;

	-- VFS PRE-FLIGHT (installer precondition P6, and NOT optional there).
	-- The attach loader 0x101b9410 is the only one of its three sibling VFS
	-- readers that does NOT check stream validity before use, so an
	-- unresolvable path is a plausible fault on the first tick rather than a
	-- harmless miss. Resolving it here, in this same VM, moments before, is
	-- the cheap way to know. Read-only: loadfile compiles, it does not run.
	local chunk_path = "data/aai/aai_attach.lua";
	local pre_fn, pre_err = loadfile(chunk_path);
	local preflight = pre_fn and "PASS (chunk resolves and compiles)"
		or ("FAIL: " .. tostring(pre_err));

	-- tag the report so battle and frontend blocks are distinguishable (both
	-- worlds are the same pid). A data/ write only -- the engine is untouched.
	local mark = io.open("data/aai_attach_probe.txt", "a");
	if mark then
		mark:write("\n---- world=" .. tostring(core.world) .. " "
			.. os.date("%Y-%m-%d %H:%M:%S") .. " ----\n");
		mark:write("PREFLIGHT " .. chunk_path .. " -> " .. preflight .. "\n");
		mark:close();
	end;
	core.log("ATTACH-PROBE preflight " .. chunk_path .. " -> " .. preflight);

	local ran, err = pcall(fn);
	if ran then
		core.log("ATTACH-PROBE ran in the " .. core.world
			.. " world -- see data/aai_attach_probe.txt");
	else
		core.log("ATTACH-PROBE ERROR: " .. tostring(err));
	end;

	-- ONE-SHOT: disarm so the probe cannot keep firing at every world load
	-- (including ordinary campaign battles) for the rest of the session.
	-- The battle world runs after the frontend, so the battle block lands first
	-- only if the lever survives -- keep it armed until a BATTLE world has run.
	if core.world == "battle" then
		pcall(os.remove, "data/aai_attach_on.txt");
	end;
end;

return M;
