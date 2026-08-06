-------------------------------------------------------------------------
--	ATTILA-AI core: shared plumbing for the campaign, battle and
--	frontend orchestrators. Nothing world-specific in here — everything
--	must work in all three Lua environments.
-------------------------------------------------------------------------

local M = {};

M.world = "unknown";	-- orchestrators set this before loading modules

local LOG_PATH = "data/attila_ai_log.txt";

function M.log(text)
	local f = io.open(LOG_PATH, "a");
	if f then
		local ts = "";
		pcall(function() ts = os.date("%H:%M:%S") .. " "; end);
		f:write("[" .. M.world .. "] " .. ts .. tostring(text) .. "\n");
		f:close();
	end;
end;

function M.log_header(text)
	M.log("");
	M.log("==== " .. tostring(text) .. " ====");
end;

-- guard a callback so a scripting mistake gets logged instead of
-- silently killing the environment's script state
function M.guarded(name, callback)
	return function(...)
		local ok, err = pcall(callback, ...);
		if not ok then
			M.log("ERROR in " .. name .. ": " .. tostring(err));
		end;
	end;
end;

-- run fn; on any error return default instead. For optimistic reads of
-- interfaces whose exact methods are unconfirmed.
function M.try(default, fn)
	local ok, value = pcall(fn);
	if ok then
		return value;
	end;
	return default;
end;

-- load a list of module files; each returns a table with init(core).
-- A broken module logs its error and is skipped, the rest keep going.
-- A module is skipped when data/aai_skip_<last path element>.txt exists
-- (aai_skip_probe.txt &c) -- the bisect lever, live in every world.
local function skipped(name)
	local slug = string.match(name, "([^/]+)$") or name;
	local fh = io.open("data/aai_skip_" .. slug .. ".txt", "r");
	if fh then
		fh:close();
		return true;
	end;
	return false;
end;

function M.load_modules(modules)
	for i = 1, #modules do
		local name = modules[i];
		if skipped(name) then
			M.log("module SKIPPED by lever: " .. name);
		else
		local ok, mod = pcall(require, name);
		if not ok then
			M.log("MODULE LOAD FAILED " .. name .. ": " .. tostring(mod));
		elseif type(mod) == "table" and type(mod.init) == "function" then
			local ok2, err = pcall(mod.init, M);
			if ok2 then
				M.log("module up: " .. name);
			else
				M.log("MODULE INIT FAILED " .. name .. ": " .. tostring(err));
			end;
		else
			M.log("module loaded (no init): " .. name);
		end;
		end;
	end;
end;

return M;
