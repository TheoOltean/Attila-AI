-------------------------------------------------------------------------
--	ATTILA-AI: first game-state probe
--
--	Loaded into the campaign script environment by the loader shims in
--	attila_ai.pack (see pack_src/). Runs with the same globals as the
--	vanilla campaign scripts: cm (campaign manager), events, output().
--
--	Everything it reads is written to <game>/data/attila_ai_log.txt.
--	Tail it from WSL:
--	  tail -f "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data/attila_ai_log.txt"
-------------------------------------------------------------------------

local LOG_PATH = "data/attila_ai_log.txt";

local function log(text)
	local f = io.open(LOG_PATH, "a");
	if f then
		f:write(tostring(text) .. "\n");
		f:close();
	end;
end;

local function log_header(text)
	log("");
	log("==== " .. tostring(text) .. " ====");
end;

-- guard a callback so a scripting mistake gets logged instead of
-- silently killing the campaign script environment
local function guarded(name, callback)
	return function(...)
		local ok, err = pcall(callback, ...);
		if not ok then
			log("ERROR in " .. name .. ": " .. tostring(err));
		end;
	end;
end;

log_header("attila_ai_main.lua loaded, campaign: " .. tostring(campaign_name) .. ", time: " .. os.date());

-------------------------------------------------------------------------
--	One-off world dump once the game world exists (first tick)
-------------------------------------------------------------------------

cm:register_first_tick_callback(guarded("first_tick dump", function()
	local model = cm:model();

	log_header("world state at first tick");
	log("turn number:      " .. tostring(model:turn_number()));
	log("difficulty level: " .. tostring(model:difficulty_level()));
	log("local faction:    " .. tostring(cm:get_local_faction()));

	local faction_list = model:world():faction_list();
	log("faction count:    " .. tostring(faction_list:num_items()));
	log("");

	for i = 0, faction_list:num_items() - 1 do
		local faction = faction_list:item_at(i);
		log(string.format("  %-45s human=%-5s treasury=%-9d regions=%d",
			faction:name(),
			tostring(faction:is_human()),
			faction:treasury(),
			faction:region_list():num_items()));
	end;
end));

-------------------------------------------------------------------------
--	Live event feed: one line whenever any faction starts its turn
-------------------------------------------------------------------------

events.FactionTurnStart[#events.FactionTurnStart + 1] = guarded("FactionTurnStart listener", function(context)
	local faction = context:faction();
	log(string.format("[turn %d] FactionTurnStart: %-45s human=%-5s treasury=%d",
		faction:model():turn_number(),
		faction:name(),
		tostring(faction:is_human()),
		faction:treasury()));
end);

log("listeners registered");
