-------------------------------------------------------------------------
--	Campaign camera telemetry: rewrites a fixed-layout snapshot of the
--	live campaign camera twice a second, for the in-place dashboard
--	(tools/watch_battle.sh). Uses the vanilla script timer system
--	(add_repeat_callback -> engine time triggers), which campaign_manager
--	initialises before our hook runs.
-------------------------------------------------------------------------

local M = {};

local SNAPSHOT_PATH = "data/attila_ai_campaign_state.txt";
local INTERVAL = 0.5;	-- seconds

local function fmt(value, width, decimals)
	if type(value) ~= "number" then
		return string.rep(" ", width - 1) .. "?";
	end;
	return string.format("%" .. width .. "." .. decimals .. "f", value);
end;

local function snapshot(core)
	local x, y, h, r;
	pcall(function() x, y, h, r = CampaignUI.GetCameraPosition(); end);
	local turn = core.try("?", function() return cm:model():turn_number(); end);

	local f = io.open(SNAPSHOT_PATH, "w");
	if f then
		f:write(string.format("==== ATTILA-AI campaign  turn %-4s ====\n", tostring(turn)));
		f:write(string.format("CAMERA  x=%s y=%s  zoom=%s  rot=%s\n",
			fmt(x, 8, 2), fmt(y, 8, 2), fmt(h, 7, 2), fmt(r, 7, 2)));
		f:close();
	end;
end;

function M.init(core)
	cm:register_first_tick_callback(core.guarded("campaign camera start", function()
		add_repeat_callback(core.guarded("campaign camera tick", function()
			snapshot(core);
		end), INTERVAL, "aai_campaign_camera");
		core.log("camera telemetry: snapshot every " .. INTERVAL .. "s -> " .. SNAPSHOT_PATH);
	end));
end;

return M;
