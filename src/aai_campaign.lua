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
	-- BATTLE-ONLY (2026-07-12): campaign feed/telemetry modules removed; only the
	-- battle launcher stays. The removed modules remain on disk under src/campaign/.
	"campaign/battle_script",  -- KEEP: attaches aai/battle_entry.lua to every campaign battle
});

-- dev hook: loose file data/aai/aai_dev.lua on the REAL disk (not the
-- pack) is loaded if present - script iteration without pack rebuilds
local dev_ok, dev = pcall(require, "aai_dev");
if dev_ok and type(dev) == "table" and type(dev.init) == "function" then
	pcall(dev.init, core);
	core.log("dev hook: loose aai_dev.lua loaded");
end;
