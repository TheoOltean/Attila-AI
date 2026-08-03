-------------------------------------------------------------------------
--	ml/game/util.lua -- tiny shared infrastructure (real, not stubs).
--
--	Engine realities baked in (SCRIPTING.md is the law doc):
--	- Lua 5.1, LUA_NUMBER = 32-bit float: integers exact only to 2^24.
--	  Every counter in this tree is a small monotonic int; never epoch time.
--	- io paths are relative to the game root; print() is invisible --
--	  the log file is the only console.
--	- One uncaught error in a callback kills the world's script state:
--	  everything driven from a callback goes through M.guarded.
-------------------------------------------------------------------------

local M = {};

M.LOG_PATH = "data/attila_ml_log.txt";

function M.log(text)
	local f = io.open(M.LOG_PATH, "a");
	if f then
		local ts = "";
		pcall(function() ts = os.date("%H:%M:%S") .. " "; end);
		f:write("[ml] " .. ts .. tostring(text) .. "\n");
		f:close();
	end;
end;

-- Flag-file lever: presence of data/<name> flips behavior with no restart.
function M.flag(name)
	local f = io.open("data/" .. name, "r");
	if f then
		f:close();
		return true;
	end;
	return false;
end;

-- pcall wrapper for callback bodies; logs the error instead of dying.
function M.guarded(tag, fn)
	return function(...)
		local ok, err = pcall(fn, ...);
		if not ok then
			M.log("GUARDED ERROR [" .. tostring(tag) .. "]: " .. tostring(err));
		end;
		return ok;
	end;
end;

function M.clamp01(x)
	if type(x) ~= "number" then return 0; end;
	if x < 0 then return 0; end;
	if x > 1 then return 1; end;
	return x;
end;

-- Zero-filled Lua array of n numbers (array position p = spec index p-1;
-- slot-index wire conversion lives ONLY in ipc/codec -- see its header).
function M.zeros(n)
	local t = {};
	for i = 1, n do t[i] = 0; end;
	return t;
end;

return M;
