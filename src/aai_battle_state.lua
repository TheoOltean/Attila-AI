-------------------------------------------------------------------------
--	ATTILA-AI orchestrator for the per-battle script state (entered via
--	battle_entry.lua / the custom-battlefield hook). empire_battle is
--	available here, so modules get the full battle interface.
--
--	To add a feature: create src/battle/<name>.lua (returning a table with
--	init(core)) and list it in src/battle/modules.lua -- shared with the
--	custom-battle kernel, which brings the same list up itself.
--
--	NOTE (2026-08-06): the custom-battle path does NOT come through here.
--	Loading this file from the payload chunk killed the engine's timer
--	dispatch every time (nine-battle bisect, reference/CUSTOM_BATTLES.md);
--	the kernel now runs this same bring-up one level up. This file remains
--	the CAMPAIGN hook's orchestrator, where it has always worked.
-------------------------------------------------------------------------

local core = require "aai_core";
core.world = "battle+";

core.log_header("battle script state loaded (custom battlefield hook)");

core.load_modules(require "battle/modules");
