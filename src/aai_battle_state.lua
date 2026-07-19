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
	"battle/api",      -- unified battle API: read_state() + issue() (providers lua+native+db)
	"battle/publish",  -- READ driver: full snapshot -> data/aai_battle.json every 500ms
	"battle/control",  -- WRITE driver. DEFAULT-OFF: self-gates on data/aai_battle_ai_on.txt,
	--   so it loads inert and the enemy AI runs normally. Create that flag file to
	--   enable external unit control; delete it to disable. No rebuild/restart to toggle.
	-- "battle/native_diff",  -- OPT-IN research tool (differential offset mapper).
	--   Uncomment for a mapping session; off by default (perf + log volume).
});
