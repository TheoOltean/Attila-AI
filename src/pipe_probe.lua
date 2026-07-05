-------------------------------------------------------------------------
--	IO path-acceptance matrix. The engine's io.open rejected the named
--	pipe (\\.\pipe\attila_ai -> "Permission denied") even though plain
--	CRT fopen on the same path works from other processes (measured
--	2026-07-05). So: map exactly which path shapes the engine's io
--	layer lets through, and whether loose Lua files on the real disk
--	load (which would kill the rebuild+restart iteration cycle).
-------------------------------------------------------------------------

local M = {};

local function try_open(core, label, path, mode, exchange)
	local f, err = io.open(path, mode);
	if not f then
		core.log("iopath: " .. label .. " FAIL: " .. tostring(err));
		return;
	end;
	if exchange then
		local ok, reply = pcall(function()
			f:write("HELLO from " .. core.world .. "\n");
			f:flush();
			return f:read("*l");
		end);
		core.log("iopath: " .. label .. " OPEN, reply = " .. tostring(reply));
	else
		f:write("marker from " .. core.world .. "\n");
		core.log("iopath: " .. label .. " OPEN (write ok)");
	end;
	f:close();
end;

function M.init(core)
	-- file paths: what shapes does the engine allow?
	try_open(core, "abs-backslash", "C:\\Users\\theod\\aai_iopath1.txt", "w");
	try_open(core, "abs-forward", "C:/Users/theod/aai_iopath2.txt", "w");
	try_open(core, "rel-updir", "..\\aai_iopath3.txt", "w");

	-- the same named pipe under three spellings Windows accepts
	try_open(core, "pipe-dos", "\\\\.\\pipe\\attila_ai", "r+", true);
	try_open(core, "pipe-fwd", "//./pipe/attila_ai", "r+", true);
	try_open(core, "pipe-unc", "\\\\localhost\\pipe\\attila_ai", "r+", true);
	try_open(core, "pipe-w-only", "\\\\.\\pipe\\attila_ai", "w");

	-- does os.* take a different code path than io.open?
	core.log("iopath: os.remove(abs1) = " .. tostring(core.try("ERR",
		function() return os.remove("C:\\Users\\theod\\aai_iopath1.txt"); end)));
	core.log("iopath: os.remove(abs2) = " .. tostring(core.try("ERR",
		function() return os.remove("C:/Users/theod/aai_iopath2.txt"); end)));
	core.log("iopath: os.remove(rel3) = " .. tostring(core.try("ERR",
		function() return os.remove("..\\aai_iopath3.txt"); end)));

	-- loose Lua files on the real disk under data/aai/
	local lf, lerr = loadfile("data/aai/dev_loose_test.lua");
	if lf then
		local ok, val = pcall(lf);
		core.log("iopath: loadfile(loose) OK -> " .. tostring(val));
	else
		core.log("iopath: loadfile(loose) FAIL: " .. tostring(lerr));
	end;
	local rok, rval = pcall(require, "dev_loose_test");
	core.log("iopath: require(loose) " ..
		(rok and ("OK -> " .. tostring(rval)) or ("FAIL: " .. tostring(rval))));
end;

return M;
