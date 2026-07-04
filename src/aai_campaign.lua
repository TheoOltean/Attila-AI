-------------------------------------------------------------------------
--	ATTILA-AI campaign orchestrator. Required by the shim at the end of
--	campaigns/main_attila/scripting.lua; runs once per campaign load,
--	after cm and the vanilla libraries exist.
--
--	To add a campaign feature: create script/campaign/<name>.lua
--	(returning a table with init(core)) and list it below.
-------------------------------------------------------------------------

local core = require "aai_core";
core.world = "campaign";

core.log_header("campaign world loaded, time: " .. core.try("?", function() return os.date(); end));

core.load_modules({
	"campaign/probe",
	"campaign/battle_script",
	"campaign/camera",
});
