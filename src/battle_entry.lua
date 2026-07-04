-------------------------------------------------------------------------
--	ATTILA-AI per-battle entry script. Attached to every campaign battle
--	by campaign/battle_script.lua via cm:add_custom_battlefield().
--
--	Runs in the battle VM with the engine's scripted-battle environment
--	layered over the raw globals table: the battle classes
--	(empire_battle, battle_vector, ...) are visible to THIS chunk's
--	global lookups but are NOT in raw _G (measured 2026-07-04 — reading
--	_G.empire_battle gives nil while a bare empire_battle call works).
--	Modules loaded at bootstrap only see raw _G, so this script must
--	acquire the interface here and hand it over via rawset into _G.
-------------------------------------------------------------------------

local function log(text)
	local f = io.open("data/attila_ai_log.txt", "a");
	if f then
		f:write("[battle+] " .. tostring(text) .. "\n");
		f:close();
	end;
end;

log("");
log("==== battle entry script running (custom battlefield hook) ====");

-- vanilla battle library (battle_manager etc.); loads into this env
local hdr_ok, hdr_err = pcall(require, "lua_scripts.Battle_Script_Header");
if not hdr_ok then
	log("Battle_Script_Header FAILED: " .. tostring(hdr_err));
end;

-- acquire the battle manager and expose it in the RAW globals table,
-- where the aai modules (whose chunks see only _G) can find it
local bm_ok, bm = pcall(function() return get_bm(); end);
if bm_ok and bm then
	rawset(_G, "aai_bm", bm);
	local count = 0;
	pcall(function() count = bm:alliances():count(); end);
	log("battle manager acquired — alliances=" .. tostring(count));
else
	log("get_bm() FAILED: " .. tostring(bm));
end;

package.path = package.path .. ";data/aai/?.lua";

local aai_ok, aai_err = pcall(require, "aai_battle_state");
if not aai_ok then
	log("ENTRY FAILED: " .. tostring(aai_err));
end;
