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
	"frontend/probe",
});
