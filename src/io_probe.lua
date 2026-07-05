-------------------------------------------------------------------------
--	IO escape-hatch probe: measures, once per world load, which routes
--	out of the Lua sandbox the engine left open. Decides the transport
--	between the game and the external AI process:
--	  1. package.loadlib  -> load a socket DLL, real TCP
--	  2. io.popen/os.execute -> spawn/talk to processes directly
--	  3. io.open file mailbox -> guaranteed fallback (already in use)
-------------------------------------------------------------------------

local M = {};

local function dump_table(core, name, t)
	if type(t) ~= "table" then
		core.log("io_probe: " .. name .. " is " .. type(t));
		return;
	end;
	local keys = {};
	for k, v in pairs(t) do
		keys[#keys + 1] = tostring(k) .. "(" .. type(v) .. ")";
	end;
	table.sort(keys);
	core.log("io_probe: " .. name .. " = " .. table.concat(keys, " "));
end;

function M.init(core)
	dump_table(core, "os", os);
	dump_table(core, "io", io);
	dump_table(core, "package", package);

	-- harmless reads first
	core.log("io_probe: os.getenv(USERNAME) = " .. tostring(core.try("ERR",
		function() return os.getenv("USERNAME"); end)));
	core.log("io_probe: os.time = " .. tostring(core.try("ERR",
		function() return os.time(); end)));

	-- io.popen for real: 'cmd /c cd' also reveals the game's cwd
	local ok, err = pcall(function()
		local f = io.popen("cmd /c cd");
		if not f then
			core.log("io_probe: io.popen returned nil");
			return;
		end;
		local line = f:read("*l");
		f:close();
		core.log("io_probe: io.popen WORKS, cwd = " .. tostring(line));
	end);
	if not ok then
		core.log("io_probe: io.popen FAILED: " .. tostring(err));
	end;

	-- os.execute return code
	local ok2, code = pcall(function() return os.execute("cmd /c exit 7"); end);
	core.log("io_probe: os.execute(exit 7) -> " ..
		(ok2 and tostring(code) or ("FAILED: " .. tostring(code))));

	-- package.loadlib: a missing DLL distinguishes a live OS loader
	-- ("module could not be found") from a stubbed/stripped function
	local ok3, lib, why = pcall(function()
		return package.loadlib("aai_no_such_library.dll", "luaopen_x");
	end);
	if ok3 then
		core.log("io_probe: loadlib(missing dll) = " .. tostring(lib) ..
			" / " .. tostring(why));
	else
		core.log("io_probe: loadlib FAILED: " .. tostring(lib));
	end;
end;

return M;
