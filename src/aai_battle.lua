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
	-- the bootstrap battle world has no engine bridge (empire_battle is
	-- injected only into attached script states, e.g. battle_entry.lua).
	"menu_probe",  -- VM census + native-door test in menu battles.
	--   DEFAULT-OFF: self-gates on data/aai_menu_probe.txt.
	"reg_walk",  -- Door B rung 1: registry hunt for the attach machinery.
	--   DEFAULT-OFF: self-gates on data/aai_reg_walk.txt.
	"attach_probe",  -- Door B rung 3: READ-ONLY native probe of the attach
	--   gate. DEFAULT-OFF: self-gates on data/aai_attach_on.txt.
});

-- dev hook: loose file data/aai/aai_dev.lua on the REAL disk (not the
-- pack) is loaded if present - script iteration without pack rebuilds
local dev_ok, dev = pcall(require, "aai_dev");
if dev_ok and type(dev) == "table" and type(dev.init) == "function" then
	pcall(dev.init, core);
	core.log("dev hook: loose aai_dev.lua loaded");
end;
