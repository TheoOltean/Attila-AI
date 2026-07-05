-------------------------------------------------------------------------
--	ATTILA-AI orchestrator for the per-battle script state (entered via
--	battle_entry.lua / the custom-battlefield hook). empire_battle is
--	available here, so modules get the full battle interface.
--
--	To add a feature for this state: create src/battle/<name>.lua
--	(returning a table with init(core)) and list it below.
-------------------------------------------------------------------------

local core = require "aai_core";
core.world = "battle+";

core.log_header("battle script state loaded (custom battlefield hook)");

core.load_modules({
	"battle/telemetry",
	"battle/state_json",
	"battle/ai_link",
});
