-------------------------------------------------------------------------
--	Spawns the external visualization server (viz/aai_viz.py) once per
--	game boot. Runs at every frontend world load; duplicate instances
--	exit immediately because the HTTP port is already bound.
-------------------------------------------------------------------------

local M = {};

local CMD = 'start "" /min "C:\\Users\\Theoo\\AppData\\Local\\Programs\\Python\\Python313\\pythonw.exe" ' ..
	'"C:\\Users\\Theoo\\programming\\Atilla-AI\\viz\\aai_viz.py"';

function M.init(core)
	local ok, code = pcall(os.execute, CMD);
	core.log("spawn_viz: os.execute -> " .. tostring(ok and code or code));
end;

return M;
