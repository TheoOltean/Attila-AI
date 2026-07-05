-------------------------------------------------------------------------
--	ATTILA-AI battle orchestrator. Required by the shim at the end of
--	lua_scripts/battle_scripted.lua; runs once per battle load.
--
--	To add a battle feature: create script/battle/<name>.lua
--	(returning a table with init(core)) and list it below.
-------------------------------------------------------------------------

local core = require "aai_core";
core.world = "battle";

core.log_header("battle world loaded");

core.load_modules({
	"battle/probe",
	"battle/telemetry",
});

-- dev hook: loose file data/aai/aai_dev.lua on the REAL disk (not the
-- pack) is loaded if present - script iteration without pack rebuilds
local dev_ok, dev = pcall(require, "aai_dev");
if dev_ok and type(dev) == "table" and type(dev.init) == "function" then
	pcall(dev.init, core);
	core.log("dev hook: loose aai_dev.lua loaded");
end;
