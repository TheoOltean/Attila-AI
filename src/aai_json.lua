-------------------------------------------------------------------------
--	Minimal JSON encoder shared by the state exporters. Tables with
--	t[1] ~= nil (or empty) encode as arrays, everything else as objects.
--	NaN/inf encode as null.
-------------------------------------------------------------------------

local M = {};

local INF = 1 / 0;	-- this Lua strips math.huge (measured 2026-07-05)

local function esc(s)
	s = string.gsub(s, "\\", "\\\\");
	s = string.gsub(s, '"', '\\"');
	s = string.gsub(s, "%c", " ");
	return s;
end;

function M.encode(v)
	local t = type(v);
	if t == "number" then
		if v ~= v or v == INF or v == -INF then
			return "null";
		end;
		return string.format("%.10g", v);
	elseif t == "string" then
		return '"' .. esc(v) .. '"';
	elseif t == "boolean" then
		return tostring(v);
	elseif t == "table" then
		local parts = {};
		if v[1] ~= nil or next(v) == nil then
			for i = 1, #v do
				parts[i] = M.encode(v[i]);
			end;
			return "[" .. table.concat(parts, ",") .. "]";
		end;
		for k, val in pairs(v) do
			parts[#parts + 1] = '"' .. esc(tostring(k)) .. '":' .. M.encode(val);
		end;
		return "{" .. table.concat(parts, ",") .. "}";
	end;
	return "null";
end;

-- write JSON to path as atomically as the Lua io lib allows
-- (tmp file + remove + rename; readers must tolerate a brief gap)
function M.write(path, value)
	local tmp = path .. ".tmp";
	local f = io.open(tmp, "w");
	if not f then
		return false;
	end;
	f:write(M.encode(value));
	f:close();
	os.remove(path);
	return os.rename(tmp, path) and true or false;
end;

return M;
