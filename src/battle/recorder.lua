-------------------------------------------------------------------------
--	Battle recorder: one .jsonl file per battle capturing the full readable
--	battle state each tick plus every command issued, so the frontend can
--	replay a battle offline. Shared module -- publish appends state, control
--	appends commands; both require this and share ONE file
--	(battle id minted by battle_entry.lua). File: data/aai_battle_<id>.jsonl.
--
--	HARD BYTE CAP (2026-07-15). A battle left sitting in the deployment phase
--	for ~6 h appended every tick forever and produced a 59 GB .jsonl. There was
--	no bound at all -- even ordinary battles reached 0.8 GB. Now: state frames
--	stop at BYTE_CAP (logged once); commands are tiny and always record. The
--	publish driver additionally only records state during the CONFLICT phase,
--	so an idle deployment can never fill a disk again.
-------------------------------------------------------------------------

local json = require "aai_json";

local M = {};

local BYTE_CAP = 512 * 1024 * 1024;	-- 512 MB/battle (~1 h of 2 Hz full-state frames)
local path = nil;
local written = 0;
local capped = false;

local function target()
	if not path then
		local id = rawget(_G, "aai_battle_id");
		path = "data/aai_battle_" .. tostring(id or "battle") .. ".jsonl";
	end;
	return path;
end;

local function note(msg)
	local f = io.open("data/attila_ai_log.txt", "a");
	if f then
		f:write("[battle+] recorder: " .. msg .. "\n");
		f:close();
	end;
end;

-- budgeted=true for bulk state frames (subject to the cap); false for commands.
local function append(tbl, budgeted)
	local ok, line = pcall(json.encode, tbl);
	if not ok then
		return;
	end;
	if budgeted then
		if capped then
			return;
		end;
		if written + #line > BYTE_CAP then
			capped = true;
			note("BYTE CAP hit (512 MB) -- state frames stopped for this battle; "
				.. "commands still recorded");
			return;
		end;
	end;
	local f = io.open(target(), "a");
	if f then
		f:write(line .. "\n");
		f:close();
		written = written + #line + 1;
	end;
end;

function M.log_geometry(geo) append(geo, true); end
function M.log_state(state) append(state, true); end
function M.log_command(cmd) append(cmd, false); end

return M;
