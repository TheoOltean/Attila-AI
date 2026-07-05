-------------------------------------------------------------------------
--	Campaign state exporter: every INTERVAL seconds writes
--	data/aai_campaign.json — turn, camera, and for every faction its
--	military forces (general display position, unit count) and owned
--	settlements (display position). Consumed by ai/aai_viz.py.
-------------------------------------------------------------------------

local M = {};

local json = require "aai_json";

local PATH = "data/aai_campaign.json";
local INTERVAL = 1.0;

local function num(fn)
	local ok, v = pcall(fn);
	if ok and type(v) == "number" then
		return v;
	end;
end;

local function snapshot(core)
	local state = { kind = "campaign" };

	pcall(function()
		local x, y, h, r = CampaignUI.GetCameraPosition();
		state.camera = { x = x, y = y, zoom = h, rot = r };
	end);
	state.turn = num(function() return cm:model():turn_number(); end);

	local factions = {};
	local list = cm:model():world():faction_list();
	for i = 0, list:num_items() - 1 do
		pcall(function()
			local faction = list:item_at(i);
			local fac = {
				name = tostring(faction:name()),
				human = faction:is_human() and true or false,
			};

			local forces = {};
			pcall(function()
				local mfl = faction:military_force_list();
				for m = 0, mfl:num_items() - 1 do
					local mf = mfl:item_at(m);
					local entry = {};
					pcall(function()
						local ch = mf:general_character();
						entry.x = ch:display_position_x();
						entry.y = ch:display_position_y();
						entry.cqi = ch:cqi();
						entry.lx = ch:logical_position_x();
						entry.ly = ch:logical_position_y();
					end);
					entry.units = num(function() return mf:unit_list():num_items(); end);
					if entry.x then
						forces[#forces + 1] = entry;
					end;
				end;
			end);
			fac.forces = forces;

			local settlements = {};
			pcall(function()
				local rl = faction:region_list();
				for r = 0, rl:num_items() - 1 do
					local region = rl:item_at(r);
					local s = {};
					pcall(function()
						s.name = tostring(region:name());
						local st = region:settlement();
						s.x = st:display_position_x();
						s.y = st:display_position_y();
						s.lx = st:logical_position_x();
						s.ly = st:logical_position_y();
					end);
					if s.x then
						settlements[#settlements + 1] = s;
					end;
				end;
			end);
			fac.settlements = settlements;

			if #forces > 0 or #settlements > 0 or fac.human then
				factions[#factions + 1] = fac;
			end;
		end);
	end;
	state.factions = factions;

	json.write(PATH, state);
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("state_json start", function()
		add_repeat_callback(core.guarded("state_json tick", function()
			snapshot(core);
		end), INTERVAL, "aai_campaign_state_json");
		core.log("campaign state_json: every " .. INTERVAL .. "s -> " .. PATH);
	end));
end;

return M;
