-------------------------------------------------------------------------
--	Spawns the external visualization server (viz/aai_viz.py) once per
--	game boot. Runs at every frontend world load; duplicate instances
--	exit immediately because the HTTP port is already bound.
--
--	The path is RESOLVED, not assumed: `start ""` returns success even when
--	its target does not exist, so a stale path spawns nothing while logging
--	"os.execute -> 0". That is exactly what happened when the custom-battles
--	worktree was folded into main (2026-08-05) and this file still pointed at
--	Atilla-AI-custom -- silent, for a whole session. Candidates are tried in
--	order and the winner is logged, so the log always says what it launched.
-------------------------------------------------------------------------

local M = {};

local PY = "C:\\Users\\Theoo\\AppData\\Local\\Programs\\Python\\Python313\\pythonw.exe";

local VIZ = {
	"C:\\Users\\Theoo\\programming\\Atilla-AI\\viz\\aai_viz.py",		-- trunk
	"C:\\Users\\Theoo\\programming\\Atilla-AI-ml\\viz\\aai_viz.py",	-- ml worktree
};

local function readable(path)
	local f = io.open(path, "r");
	if f then
		f:close();
		return true;
	end;
	return false;
end;

function M.init(core)
	local viz = nil;
	for _, path in ipairs(VIZ) do
		if readable(path) then
			viz = path;
			break;
		end;
	end;
	if not viz then
		core.log("spawn_viz: NO cockpit found -- tried " .. table.concat(VIZ, " ; ")
			.. " (retired worktree? run it by hand: py viz/aai_viz.py)");
		return;
	end;
	local py = readable(PY) and PY or "pythonw.exe";	-- PATH fallback
	local ok, code = pcall(os.execute, 'start "" /min "' .. py .. '" "' .. viz .. '"');
	core.log("spawn_viz: " .. viz .. " via " .. py .. " -> "
		.. tostring(ok and code or code));
end;

return M;
