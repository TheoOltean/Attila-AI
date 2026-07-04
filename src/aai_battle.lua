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
