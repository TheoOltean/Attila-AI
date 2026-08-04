-------------------------------------------------------------------------
--	ATTILA-AI scenario start script: the attached battle script named by
--	pack_src/script/aai_scenario/aai_battle.xml (<battle_script>), which
--	the AAI_Scenario battles-table row points at. If the menu battle door
--	works, the engine runs THIS chunk with the injected battle interface
--	environment -- the same privilege battle_entry.lua gets in campaign
--	battles via add_custom_battlefield.
--
--	Default = first-light census only (prove the door with minimal
--	machinery). Flag file data/aai_scn_full.txt additionally chain-loads
--	battle_entry.lua into this env for the full production stack
--	(feed/harness/native); flags are re-read on every battle load, so
--	both tests fit in one game session.
-------------------------------------------------------------------------

local function log(text)
	local f = io.open("data/attila_ai_log.txt", "a");
	if f then
		local ts = "";
		pcall(function() ts = os.date("%H:%M:%S") .. " "; end);
		f:write("[scenario] " .. ts .. tostring(text) .. "\n");
		f:close();
	end;
end;

local function flag(name)
	local f = io.open("data/" .. name, "r");
	if f then
		f:close();
		return true;
	end;
	return false;
end;

log("");
log("==== AAI scenario start script running (menu battle door) ====");

local hdr_ok, hdr_err = pcall(require, "lua_scripts.Battle_Script_Header");
log("Battle_Script_Header require: " .. tostring(hdr_ok)
	.. (hdr_ok and "" or (" -- " .. tostring(hdr_err))));

local bm_ok, bm = pcall(function() return get_bm(); end);
if not (bm_ok and bm) then
	log("get_bm FAILED: " .. tostring(bm));
	log("DOOR VERDICT: chunk ran but interface absent");
	return;
end;

rawset(_G, "aai_bm", bm);
rawset(_G, "aai_env", getfenv(1));
rawset(_G, "aai_bless", function(fn) return setfenv(fn, getfenv(1)); end);
log("battle manager acquired -- DOOR OPEN");

-- first-light census: load-time context, where engine interface calls
-- are legal (the context law)
local census_ok, census_err = pcall(function()
	local battle = bm.battle;
	local alliances = battle:alliances();
	log("alliances: " .. alliances:count());
	for i = 1, alliances:count() do
		local armies = alliances:item(i):armies();
		local units = 0;
		for j = 1, armies:count() do
			units = units + armies:item(j):units():count();
		end;
		log("  alliance " .. i .. ": " .. armies:count()
			.. " armies, " .. units .. " units");
	end;
	local u = alliances:item(1):armies():item(1):units():item(1);
	local p = u:position();
	log("  first unit: " .. tostring(u:name()) .. " type=" .. tostring(u:type())
		.. " at " .. p:get_x() .. "," .. p:get_z());
end);
if not census_ok then
	log("census FAILED: " .. tostring(census_err));
end;

if flag("aai_scn_full.txt") then
	-- chain-load the campaign battle entry chunk into THIS env: if
	-- loadfile resolves through the pack VFS, this boots the whole
	-- production stack (publish/harness/native) in a menu battle.
	local chunk, lf_err = loadfile("data/aai/battle_entry.lua");
	if chunk then
		setfenv(chunk, getfenv(1));
		local run_ok, run_err = pcall(chunk);
		log("battle_entry chain-load: " .. tostring(run_ok)
			.. (run_ok and "" or (" -- " .. tostring(run_err))));
	else
		log("loadfile(data/aai/battle_entry.lua) FAILED: " .. tostring(lf_err));
	end;
else
	log("full-stack chain-load skipped (no aai_scn_full.txt)");
end;
