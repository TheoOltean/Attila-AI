-------------------------------------------------------------------------
--	Campaign state exporter: every INTERVAL seconds writes
--	data/aai_campaign.json — turn, camera, and for every faction its
--	military forces (general display position, unit count) and owned
--	settlements (display position). Consumed by viz/aai_viz.py.
-------------------------------------------------------------------------

local M = {};

local json = require "aai_json";

local PATH = "data/aai_campaign.json";
local INTERVAL = 1.0;

-- income proxy: no faction:income() getter exists, so track treasury at each
-- turn boundary and report the delta (net gold change over the last full turn).
local gold_snap = {};   -- faction name -> treasury at start of current turn
local gold_net = {};    -- faction name -> last full-turn delta
local snap_turn = nil;

local function num(fn)
	local ok, v = pcall(fn);
	if ok and type(v) == "number" then
		return v;
	end;
end;

local function boolean(fn)
	local ok, v = pcall(fn);
	if ok then return v and true or false; end;
end;

local function snapshot(core)
	local state = { kind = "campaign" };

	pcall(function()
		local x, y, h, r = CampaignUI.GetCameraPosition();
		state.camera = { x = x, y = y, zoom = h, rot = r };
	end);
	state.turn = num(function() return cm:model():turn_number(); end);
	local new_turn = (state.turn ~= nil and state.turn ~= snap_turn);
	local cur_gold = {};
	local focus = (rawget(_G, "AAI_TAKEOVER") or {}).faction;

	local factions = {};
	local list = cm:model():world():faction_list();
	for i = 0, list:num_items() - 1 do
		pcall(function()
			local faction = list:item_at(i);
			local fac = {
				name = tostring(faction:name()),
				human = faction:is_human() and true or false,
			};
			fac.gold = num(function() return faction:treasury(); end);
			fac.tax = num(function() return faction:tax_level(); end);
			fac.at_war = boolean(function() return faction:at_war(); end);
			fac.allies = num(function() return faction:num_allies(); end);
			fac.religion_pct = num(function() return faction:state_religion_percentage(); end);
			if fac.gold ~= nil then
				cur_gold[fac.name] = fac.gold;
				if new_turn and gold_snap[fac.name] ~= nil then
					gold_net[fac.name] = fac.gold - gold_snap[fac.name];
				end;
			end;
			fac.net = gold_net[fac.name];

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
					entry.ap = num(function() return mf:general_character():action_points_remaining_percent(); end);
					entry.upkeep = num(function() return mf:upkeep(); end);
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
						s.public_order = num(function() return region:public_order(); end);
					end);
					if s.x and focus and fac.name == focus then
						local blds = {};
						pcall(function()
							local slots = region:slot_list();
							for si = 0, slots:num_items() - 1 do
								local slot = slots:item_at(si);
								local b = { slot = tostring(slot:name()) };
								if slot:has_building() then
									local bld = slot:building();
									b.key = tostring(bld:name());
									pcall(function() b.chain = tostring(bld:superchain()); end);
								end;
								blds[#blds + 1] = b;
							end;
						end);
						s.buildings = blds;
					end;
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
	if new_turn then gold_snap = cur_gold; snap_turn = state.turn; end;
	state.factions = factions;

	local tk = rawget(_G, "AAI_TAKEOVER");
	if tk then
		local taken = {};
		if tk.taken then for k in pairs(tk.taken) do taken[#taken + 1] = k; end; end;
		state.takeover = {
			on = tk.on and true or false,
			faction = tk.faction,
			turn = tk.turn,
			taken = taken,
		};
	end;

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
