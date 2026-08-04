-------------------------------------------------------------------------
--	Spawns the external visualization server (viz/aai_viz.py) once per
--	game boot. Runs at every frontend world load; duplicate instances
--	exit immediately because the HTTP port is already bound.
-------------------------------------------------------------------------

local M = {};

-- THIS BRANCH's viz (worktree Atilla-AI-custom): the pack built from a
-- worktree must spawn that worktree's cockpit, or the port gets held by a
-- viz without the branch's routes (3-day-stale main viz caught 08-04 --
-- it had no arm button, so custom battles could never arm from the page)
local CMD = 'start "" /min "C:\\Users\\Theoo\\AppData\\Local\\Programs\\Python\\Python313\\pythonw.exe" ' ..
	'"C:\\Users\\Theoo\\programming\\Atilla-AI-custom\\viz\\aai_viz.py"';

function M.init(core)
	local ok, code = pcall(os.execute, CMD);
	core.log("spawn_viz: os.execute -> " .. tostring(ok and code or code));
end;

return M;
