-------------------------------------------------------------------------
--	Campaign spawner (Theo 2026-08-01): grant arbitrary units into EXISTING
--	armies -- his own or the enemy's -- for composing test matchups.
--	Mailbox pattern = actions.lua (id-deduped lines, file removed at first
--	tick so a stale session's orders never replay a non-idempotent grant).
--
--	  scan  <id>                  -> rewrite data/aai_armies.json
--	  grant <id> <cqi> <unit_key> -> cm:grant_unit("character_cqi:"..cqi, key)
--	                                 (lookup format = vanilla's own
--	                                 lib_misc_campaign.lua:1323; binding in
--	                                 ghidra lua_accessor_map. Arg order is
--	                                 the one unverified guess -- an ERR ack
--	                                 on a valid army+key means flip the args)
--
--	Acks -> data/aai_camp_ack.json (ring of 40). Any successful grant
--	triggers one rescan at the end of the batch, so the army's unit count/
--	roster in the feed is the EFFECT check (OK-ack alone proves nothing --
--	the silent-accept law).
-------------------------------------------------------------------------

local M = {};

local json = require "aai_json";

local ORD = "data/aai_camp_order.txt";
local ACK = "data/aai_camp_ack.json";
local ARMIES = "data/aai_armies.json";
local INTERVAL = 0.5;

local applied = {};
local acks = {};
local last_id = 0;

local function push_ack(id, verb, ok, msg)
	acks[#acks + 1] = { id = id, verb = verb, ok = ok and true or false,
		msg = (msg ~= nil) and tostring(msg) or nil };
	while #acks > 40 do
		table.remove(acks, 1);
	end;
	if id > last_id then
		last_id = id;
	end;
	json.write(ACK, { last = last_id, acks = acks });
end;

-- Full army walk: the faction -> military_force_list -> general_character
-- chain is state_json.lua's proven-live pattern. Garrisons (no general)
-- are skipped -- grant_unit needs a character to grant to.
local function scan(core)
	local rows = {};
	local turn;
	pcall(function() turn = cm:model():turn_number(); end);
	local list = cm:model():world():faction_list();
	for i = 0, list:num_items() - 1 do
		pcall(function()
			local faction = list:item_at(i);
			local fname = tostring(faction:name());
			local human = faction:is_human() and true or false;
			local at_war = false;
			pcall(function() at_war = faction:at_war() and true or false; end);
			local mfl = faction:military_force_list();
			for m = 0, mfl:num_items() - 1 do
				pcall(function()
					local mf = mfl:item_at(m);
					local ch = mf:general_character();
					local e = { faction = fname, human = human,
						at_war = at_war, cqi = ch:cqi() };
					pcall(function()
						e.x = ch:display_position_x();
						e.y = ch:display_position_y();
					end);
					pcall(function() e.region = tostring(ch:region():name()); end);
					local units = {};
					pcall(function()
						local ul = mf:unit_list();
						e.n = ul:num_items();
						for u = 0, ul:num_items() - 1 do
							pcall(function()
								units[#units + 1] = tostring(ul:item_at(u):unit_key());
							end);
						end;
					end);
					e.units = units;
					if e.cqi ~= nil then
						rows[#rows + 1] = e;
					end;
				end);
			end;
		end);
	end;
	json.write(ARMIES, { turn = turn, clock = os.clock(), armies = rows });
	return #rows;
end;

local function process(core, text)
	local granted = false;
	local scanned = false;
	for line in string.gmatch(text, "[^\r\n]+") do
		local kind, id, rest = string.match(line, "^(%a+) (%d+) ?(.*)$");
		if id then
			id = tonumber(id);
			if not applied[id] then
				applied[id] = true;
				if kind == "scan" then
					local ok, n = pcall(scan, core);
					scanned = true;
					push_ack(id, "scan", ok, ok and (n .. " armies") or n);
					core.log("spawner: scan -> " .. tostring(n));
				elseif kind == "grant" then
					local cqi, key = string.match(rest, "^(%d+) (%S+)$");
					if cqi then
						local ok, err = pcall(function()
							cm:grant_unit("character_cqi:" .. cqi, key);
						end);
						granted = granted or ok;
						push_ack(id, "grant", ok, ok and (key .. " -> cqi " .. cqi) or err);
						core.log("spawner: grant " .. key .. " -> cqi " .. cqi ..
							(ok and " OK" or (" FAILED " .. tostring(err))));
					else
						push_ack(id, "grant", false, "bad args (want: cqi unit_key)");
					end;
				else
					push_ack(id, kind, false, "unknown verb");
				end;
			end;
		end;
	end;
	-- one effect-check rescan per batch (skip if the batch already scanned)
	if granted and not scanned then
		pcall(scan, core);
	end;
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("spawner start", function()
		pcall(os.remove, ORD);
		add_repeat_callback(core.guarded("spawner tick", function()
			local f = io.open(ORD, "r");
			if f then
				local text = f:read("*a");
				f:close();
				pcall(process, core, text or "");
			end;
		end), INTERVAL, "aai_campaign_spawner");
		local ok, n = pcall(scan, core);
		core.log("spawner: polling " .. ORD .. " every " .. INTERVAL ..
			"s; initial scan -> " .. tostring(n) .. " armies");
	end));
end;

return M;
