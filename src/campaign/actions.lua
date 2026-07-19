-------------------------------------------------------------------------
--	Faction actions: apply the management commands the page issues for a
--	focused faction. Reads data/aai_faction_actions.txt -- lines are
--	"<kind> <id> <args>", de-duplicated by id (so replayed lines apply once
--	and non-idempotent ones like gold never double-apply).
--
--	  tax  <id> <faction> <level>        cm:set_tax_rate
--	  dip  <id> <a> <b> <peace|war>      cm:force_diplomacy(a,b,deal,true,true)
--	  gold <id> <faction> <amount>       cm:treasury_mod (may be negative)
--
--	Building upgrades and agent actions are intentionally absent: the
--	campaign Lua API exposes no writer for them (verified 2026-07-08).
-------------------------------------------------------------------------

local M = {};

local PATH = "data/aai_faction_actions.txt";
local INTERVAL = 0.4;

local applied = {};

local function apply(core, text)
	for line in string.gmatch(text, "[^\r\n]+") do
		local kind, id, rest = string.match(line, "^(%a+) (%d+) (.+)$");
		if id then
			id = tonumber(id);
			if not applied[id] then
				applied[id] = true;
				if kind == "tax" then
					local fac, lvl = string.match(rest, "^(%S+) (%d+)");
					if fac then
						local ok, err = pcall(function() cm:set_tax_rate(fac, tonumber(lvl)); end);
						core.log("actions: tax " .. fac .. " -> " .. lvl ..
							(ok and "" or (" FAILED " .. tostring(err))));
					end;
				elseif kind == "dip" then
					local a, b, deal = string.match(rest, "^(%S+) (%S+) (%a+)");
					if a then
						local dt = (deal == "peace") and "peace" or "war";
						local ok, err = pcall(function() cm:force_diplomacy(a, b, dt, true, true); end);
						core.log("actions: dip " .. a .. " " .. dt .. " " .. b ..
							(ok and "" or (" FAILED " .. tostring(err))));
					end;
				elseif kind == "gold" then
					local fac, amt = string.match(rest, "^(%S+) (%-?%d+)");
					if fac then
						local ok, err = pcall(function() cm:treasury_mod(fac, tonumber(amt)); end);
						core.log("actions: gold " .. fac .. " " .. amt ..
							(ok and "" or (" FAILED " .. tostring(err))));
					end;
				end;
			end;
		end;
	end;
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("actions start", function()
		pcall(os.remove, PATH);
		add_repeat_callback(core.guarded("actions tick", function()
			local f = io.open(PATH, "r");
			if f then
				local text = f:read("*a"); f:close();
				pcall(apply, core, text or "");
			end;
		end), INTERVAL, "aai_campaign_actions");
		core.log("actions: polling " .. PATH .. " every " .. INTERVAL .. "s");
	end));
end;

return M;
