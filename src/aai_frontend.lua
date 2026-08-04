-------------------------------------------------------------------------
--	ATTILA-AI frontend orchestrator. Required by the shim at the end of
--	lua_scripts/frontend_scripted.lua; runs once each time the main
--	menu world loads.
--
--	To add a frontend feature: create script/frontend/<name>.lua
--	(returning a table with init(core)) and list it below.
-------------------------------------------------------------------------

local core = require "aai_core";
core.world = "frontend";

core.log_header("frontend world loaded");

core.load_modules({
	"frontend/spawn_viz",
	"menu_probe",  -- VM census + native-door test (custom-battle setup
	--   lives in this world). DEFAULT-OFF: gates on data/aai_menu_probe.txt.
});

-- dev hook: loose file data/aai/aai_dev.lua on the REAL disk (not the
-- pack) is loaded if present - script iteration without pack rebuilds
local dev_ok, dev = pcall(require, "aai_dev");
if dev_ok and type(dev) == "table" and type(dev.init) == "function" then
	pcall(dev.init, core);
	core.log("dev hook: loose aai_dev.lua loaded");
end;
