-------------------------------------------------------------------------
--	AI takeover: sub the campaign AI out and let the player issue the
--	actions instead. We suppress MOVEMENT for every non-human faction
--	(cm:disable_movement_for_faction) so the end-turn AI moves nothing;
--	the player then drives any faction's armies from the viz map through
--	the existing move channel (src/campaign/ai_link.lua -> cm:move_to).
--
--	Why this and not a per-faction turn pause: the grand campaign engine
--	OWNS AI turn advancement -- withholding cm:end_turn() does not gate it
--	(only the scripted prologue campaigns can). disable_movement_for_faction
--	is exactly the lever the shipped prologue uses to stop the AI acting
--	during its end-turn sequence (pro_turn_one.lua:119/141-145: "prevent
--	them from moving, as they will try to during their end-turn sequences").
--
--	Suppression is (re)asserted at the START of the player's turn (so it is
--	already in force when the end-turn sequence runs) and again at each AI
--	faction's own turn-start (belt and braces). ai_link re-enables movement
--	per-character just for the army it is ordering, so player commands still
--	execute while the un-commanded rest of the AI stays frozen.
--
--	Control file data/aai_campaign_control.txt (one word, seq-gated):
--	  on   -> takeover on  (suppress all AI movement)
--	  off  -> takeover off (restore the AI)
--	  next -> fly the camera to the next AI faction (to inspect / command it)
-------------------------------------------------------------------------

local M = {};

local CONTROL_PATH = "data/aai_campaign_control.txt";
local INTERVAL = 0.4;

-- shared state, read by campaign/state_json.lua for the page
local S = { on = false, faction = "", turn = 0, walk_idx = 0, taken = {} };
rawset(_G, "AAI_TAKEOVER", S);

local last_seq = 0;

local function each_ai_faction(fn)
	local ok, list = pcall(function() return cm:model():world():faction_list(); end);
	if not ok or not list then return; end;
	for i = 0, list:num_items() - 1 do
		local okf, f = pcall(function() return list:item_at(i); end);
		if okf and f then
			local human = false;
			pcall(function() human = f:is_human(); end);
			if not human then pcall(fn, f); end;
		end;
	end;
end;

-- Suppression is per-FOCUSED-faction, NEVER global: disabling all ~117
-- factions crashes the engine during end-turn processing (the shipped
-- prologue only ever disables a handful). S.taken is the taken-over set.
local function take_over(core, name)
	if not name or name == "" then return; end;
	pcall(function() cm:disable_movement_for_faction(name); end);
	S.taken[name] = true;
	core.log("takeover: took over " .. name .. " (movement suppressed)");
end;

local function release_faction(core, name)
	if not name or name == "" then return; end;
	pcall(function() cm:enable_movement_for_faction(name); end);
	S.taken[name] = nil;
	core.log("takeover: released " .. name);
end;

local function reassert(core)
	for name in pairs(S.taken) do
		pcall(function() cm:disable_movement_for_faction(name); end);
	end;
end;

local function jump_camera_to_faction(faction)
	pcall(function()
		local mfl = faction:military_force_list();
		for m = 0, mfl:num_items() - 1 do
			local ch = mfl:item_at(m):general_character();
			local x, y = ch:display_position_x(), ch:display_position_y();
			if x then CampaignUI.SetCameraTargetInstant(x, y); return; end;
		end;
		local rl = faction:region_list();
		for r = 0, rl:num_items() - 1 do
			local st = rl:item_at(r):settlement();
			local x, y = st:display_position_x(), st:display_position_y();
			if x then CampaignUI.SetCameraTargetInstant(x, y); return; end;
		end;
	end);
end;

-- fly the camera to the next AI faction so the player can inspect / command it
local function walk_next(core)
	local facs = {};
	each_ai_faction(function(f) facs[#facs + 1] = f; end);
	if #facs == 0 then return; end;
	S.walk_idx = (S.walk_idx % #facs) + 1;
	local f = facs[S.walk_idx];
	S.faction = core.try("?", function() return tostring(f:name()); end);
	S.on = (S.faction ~= "" and S.taken[S.faction]) and true or false;
	jump_camera_to_faction(f);
	core.log("takeover: camera -> " .. S.faction .. " (" .. S.walk_idx .. "/" .. #facs .. ")");
end;

local function apply_control(core, text)
	local seq = tonumber(string.match(text, "seq (%d+)"));
	if not seq or seq <= last_seq then return; end;
	last_seq = seq;
	local cmd, arg = string.match(text, "seq %d+%s+(%a+)%s*(%S*)");
	local nm = (arg and arg ~= "") and arg or S.faction;
	if cmd == "on" then
		S.on = true; S.faction = nm;
		take_over(core, nm);
	elseif cmd == "off" then
		S.on = false;
		release_faction(core, nm);
	elseif cmd == "focus" then
		if nm and nm ~= "" then S.faction = nm; end;
	elseif cmd == "next" then
		walk_next(core);
	end;
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("takeover start", function()
		pcall(os.remove, CONTROL_PATH);
		last_seq = 0;

		-- re-assert suppression at each AI faction's own turn-start
		cm:set_default_turn_start_callback(core.guarded("takeover ai-turn", function(context)
			S.turn = core.try(S.turn, function() return cm:model():turn_number(); end);
			local nm = core.try(nil, function() return context:faction():name(); end);
			if nm and S.taken[nm] then
				pcall(function() cm:disable_movement_for_faction(nm); end);
			end;
		end));

		-- (re)assert suppression at the START of the player's turn, before the
		-- end-turn AI sequence runs
		cm:add_turn_start_callback(
			"aai_takeover_human",
			function(context) return context:faction():is_human(); end,
			core.guarded("takeover human-turn", function()
				S.turn = core.try(S.turn, function() return cm:model():turn_number(); end);
				reassert(core);
			end),
			true);

		-- nothing suppressed on load; takeover is opt-in per faction

		add_repeat_callback(core.guarded("takeover tick", function()
			local f = io.open(CONTROL_PATH, "r");
			if f then
				local text = f:read("*a"); f:close();
				pcall(apply_control, core, text or "");
			end;
		end), INTERVAL, "aai_campaign_takeover");

		core.log("takeover: installed (on=" .. tostring(S.on) .. "); polling " ..
			CONTROL_PATH .. " every " .. INTERVAL .. "s");
	end));
end;

return M;
