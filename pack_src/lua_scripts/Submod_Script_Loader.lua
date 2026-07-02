-- ATTILA-AI override of TDD's Submod_Script_Loader.
--
-- TDD's campaign scripting.lua (campaigns/main_attila_map/scripting.lua) ends with
-- require "lua_scripts.Submod_Script_Loader" precisely so submods can inject scripts
-- without replacing the whole campaign script. This override chains into the
-- Attila-AI project scripts, which live loose in data/script
-- (an NTFS junction pointing at C:\Users\theod\programming\Attila-AI\script).

output("**** Submod_Script_Loader: Attila-AI override active ****");

package.path = package.path .. ";data/script/?.lua";

local ok, err = pcall(require, "attila_ai_main");

if ok then
	output("**** Submod_Script_Loader: attila_ai_main.lua loaded ****");
else
	output("**** Submod_Script_Loader: FAILED to load attila_ai_main.lua: " .. tostring(err) .. " ****");
	-- also record the failure in our own log, visible without game logging enabled
	local f = io.open("data/attila_ai_log.txt", "a");
	if f then
		f:write("LOADER ERROR (TDD Submod_Script_Loader): " .. tostring(err) .. "\n");
		f:close();
	end;
end;
