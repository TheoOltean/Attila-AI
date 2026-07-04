-------------------------------------------------------------------------
--	Campaign probe: minimal telemetry — the local (player) faction's
--	settlements and army positions only, once at first tick and once at
--	the start of every round.
-------------------------------------------------------------------------

local M = {};

local function find_local_faction()
	local factions = cm:model():world():faction_list();
	for i = 0, factions:num_items() - 1 do
		local faction = factions:item_at(i);
		if faction:is_human() then
			return faction;
		end;
	end;
	return nil;
end;

local function dump(core, reason)
	local faction = find_local_faction();
	if not faction then
		core.log("no human faction found (" .. reason .. ")");
		return;
	end;

	core.log_header(string.format("turn %s (%s) — %s",
		tostring(cm:model():turn_number()), reason, faction:name()));

	local regions = faction:region_list();
	for r = 0, regions:num_items() - 1 do
		core.log("  settlement: " .. regions:item_at(r):name());
	end;

	local characters = faction:character_list();
	for c = 0, characters:num_items() - 1 do
		local character = characters:item_at(c);
		if core.try(false, function() return character:has_military_force(); end) then
			core.log(string.format("  army at (%s,%s)  units=%s",
				tostring(core.try("?", function() return character:logical_position_x(); end)),
				tostring(core.try("?", function() return character:logical_position_y(); end)),
				tostring(core.try("?", function() return character:military_force():unit_list():num_items(); end))));
		end;
	end;
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("campaign probe first tick", function()
		dump(core, "first tick");
	end));

	events.FactionTurnStart[#events.FactionTurnStart + 1] =
		core.guarded("campaign probe FactionTurnStart", function(context)
			if context:faction():is_human() then
				dump(core, "new round");
			end;
		end);
end;

return M;
