-------------------------------------------------------------------------
--	Attaches aai/battle_entry.lua to every battle fought in the campaign
--	via the custom-battlefield mechanism: a battlefield record centred
--	at (0,0) with a radius covering the whole map, whose only override
--	is the script. The engine then runs that script in the per-battle
--	state (where empire_battle exists) for every campaign battle.
-------------------------------------------------------------------------

local M = {};

local ID = "aai_battle_hook";
local SCRIPT = "aai/battle_entry.lua";

function M.init(core)
	cm:register_first_tick_callback(core.guarded("battle script hook", function()
		-- re-registering after a save/load would duplicate the record
		pcall(function() cm:remove_custom_battlefield(ID); end);
		cm:add_custom_battlefield(
			ID,			-- string identifier
			0, 0,		-- x, y
			1000000,	-- radius: everywhere
			false,		-- dump campaign to disk
			"",			-- loading screen override
			SCRIPT,		-- battle script override
			"",			-- whole-battle (setup xml) override
			0,			-- human alliance when battle override used
			false,		-- launch battle immediately
			false);
		core.log("custom battlefield hook registered: " .. SCRIPT);
	end));
end;

return M;
