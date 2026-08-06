-------------------------------------------------------------------------
--	The battle-state module list, in bring-up order. PASSIVE: this file
--	must never call the engine or run work at load -- both orchestrators
--	read it (aai_battle_state.lua for the campaign hook, the custom-battle
--	KERNEL for its own bring-up), and the kernel pre-stuffs it from disk.
--
--	Skipping is not done here: core.load_modules honours a per-module
--	lever, data/aai_skip_<last path element>.txt (e.g. aai_skip_probe.txt).
-------------------------------------------------------------------------

return {
	"battle/api",      -- unified battle API: read_state() + issue() (providers lua+native+db)
	"battle/publish",  -- READ driver: full snapshot -> data/aai_battle.json every 500ms
	-- "battle/control" RETIRED 2026-07-29 with the cockpit AI-control UI (Theo:
	--   "of no use to me anymore") -- the harness is the single write path now.
	"battle/probe",    -- T1 capability probe harness. DEFAULT-OFF: self-gates on
	--   data/aai_probe_on.txt (create to arm, delete to disarm). Exercises every
	--   unverified T1 read/write on one enemy guinea-pig unit and logs verdicts
	--   (grep log for "PROBE"); results also in data/aai_probe.json.
	"battle/harness",  -- interactive capability tester (the cockpit /harness page).
	--   DEFAULT-OFF: self-gates on data/aai_harness_on.txt. Applies one test
	--   order at a time from data/aai_test_order.txt, acks to aai_test_ack.json.
	-- "battle/native_diff",  -- OPT-IN research tool (differential offset mapper).
	--   Uncomment for a mapping session; off by default (perf + log volume).
};
