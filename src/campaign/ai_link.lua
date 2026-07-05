-------------------------------------------------------------------------
--	Campaign AI link: applies army movement orders written by the
--	external process (drags on the viz map). Same mailbox pattern as
--	the battle ai_link: poll each tick, apply batches with a fresh seq.
--
--	Orders file (data/aai_campaign_orders.txt):
--	  seq <ms>
--	  move <cqi> <logical_x> <logical_y> <faction_name>
--
--	Recipe from vanilla lib_misc_campaign.lua move_npc_army(): enable
--	movement + replenish AP + cm:move_to — works for ANY faction's
--	army during the player turn, which is what "override the AI" needs.
-------------------------------------------------------------------------

local M = {};

local ORDERS_PATH = "data/aai_campaign_orders.txt";
local INTERVAL = 0.5;

local last_seq = 0;

local function apply(core, text)
	local seq = tonumber(string.match(text, "^seq (%d+)"));
	if not seq or seq <= last_seq then
		return;
	end;
	last_seq = seq;

	for line in string.gmatch(text, "[^\r\n]+") do
		local cqi, x, y, faction =
			string.match(line, "^move (%d+) ([-%d%.]+) ([-%d%.]+) (%S+)");
		if cqi then
			local char_str = "faction:" .. faction .. ",character_cqi:" .. cqi;
			local ok, err = pcall(function()
				pcall(function() cm:enable_movement_for_character(char_str); end);
				pcall(function() cm:replenish_action_points(char_str); end);
				cm:move_to(char_str, tonumber(x), tonumber(y), true);
			end);
			if ok then
				core.log("campaign ai_link: move " .. char_str ..
					" -> " .. x .. ", " .. y);
			else
				core.log("campaign ai_link: move FAILED (" .. char_str ..
					"): " .. tostring(err));
			end;
		end;
	end;
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("campaign ai_link start", function()
		pcall(os.remove, ORDERS_PATH);
		add_repeat_callback(core.guarded("campaign ai_link tick", function()
			local f = io.open(ORDERS_PATH, "r");
			if f then
				local text = f:read("*a");
				f:close();
				local ok, err = pcall(apply, core, text or "");
				if not ok then
					core.log("campaign ai_link: apply ERROR: " .. tostring(err));
				end;
			end;
		end), INTERVAL, "aai_campaign_ai_link");
		core.log("campaign ai_link: polling " .. ORDERS_PATH ..
			" every " .. INTERVAL .. "s");
	end));
end;

return M;
