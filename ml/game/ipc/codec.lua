-------------------------------------------------------------------------
--	ml/game/ipc/codec.lua -- frame encode/decode (game side).
--
--	Direction split (engine reality): Lua -> host is JSON text (encoder
--	only -- ~60 lines, hand-rolled, numbers %.10g, NaN/inf -> null);
--	host -> Lua is a LINE grammar (this Lua has NO JSON decoder and
--	never will need one).
--
--	Index law (precise form): SLOT indices (u / enemy / struct /
--	vehicle) are 0-based on the wire and converted to roster/index
--	positions HERE, exactly once. VOCAB indices (shot / stance /
--	formation / ability) pass through 0-based untouched -- the write
--	surface owns the vocab-index -> engine-key lookup (its +1 into the
--	spec tables is a table lookup, not a re-conversion).
--
--	Wire verb keys are spec.VERBS keys EXACTLY ("move_formation" -- not
--	the "move_form" shorthand in ML_DESIGN's example record).
--
--	Obs frame (data/aai_ml_obs.json), one JSON object per decision tick:
--	  { v, battle_id, seq, tick, phase, clock,
--	    applied_seq, apply_errs,          -- act feedback piggybacked
--	    global = [40],
--	    friendly = { types = [40], feat = [[224] x 40] },
--	    enemy   = { types = [40], feat = [[224] x 40] },
--	    structures = [[16] x 512], vehicles = [[12] x 32],
--	    aux = { centroid = {x, y}, raster_events = [...] } }
--
--	Hello frame (data/aai_ml_hello.json), once per battle:
--	  { v, battle_id, map = {key, w, h, min_z}, shapes = spec.shapes() }
--
--	Ack frame (data/aai_ml_ack.json): { last, ring = [entry x <= 40] },
--	one entry per act FRAME with per-ACTION results (matching what
--	write.apply returns): { seq, tick, phase, ok, err,
--	  results = [{u, verb, ok, err} ...] } -- frame-level ok/err covers
--	parse/phase rejections where no action ran. A ring, never one-slot.
--
--	Act frame (data/aai_ml_act.txt), whitespace-tokenized lines:
--	  frame <seq> <battle_id> <for_tick>
--	  u <slot> <verb> <args per spec.ARG_TOKENS[verb.args]>
--	  ...
--	  end <action_count>
--	Host writes it atomically (os.replace) -- a parsed frame is complete
--	by construction; the end-count is a belt against host bugs.
-------------------------------------------------------------------------

local spec = require "ml/spec";

local M = {};

local function esc(s)
	s = string.gsub(s, "\\", "\\\\");
	s = string.gsub(s, '"', '\\"');
	s = string.gsub(s, "%c", " ");
	return s;
end;

-- Lua table -> JSON string. Array iff t[1] ~= nil or empty ({} -> []).
function M.json(t)
	local ty = type(t);
	if ty == "nil" then return "null"; end;
	if ty == "boolean" then return t and "true" or "false"; end;
	if ty == "number" then
		if t ~= t or t == math.huge or t == -math.huge then return "null"; end;
		return string.format("%.10g", t);
	end;
	if ty == "string" then return '"' .. esc(t) .. '"'; end;
	if ty == "table" then
		local parts = {};
		if t[1] ~= nil or next(t) == nil then
			for i = 1, #t do
				parts[#parts + 1] = M.json(t[i]);
			end;
			return "[" .. table.concat(parts, ",") .. "]";
		end;
		for k, v in pairs(t) do
			parts[#parts + 1] = '"' .. esc(tostring(k)) .. '":' .. M.json(v);
		end;
		return "{" .. table.concat(parts, ",") .. "}";
	end;
	return "null";
end;

function M.encode_hello(battle_id, map)
	return M.json({
		v = spec.SPEC_VERSION,
		battle_id = battle_id,
		map = map,
		shapes = spec.shapes(),
	});
end;

-- header carries identity + act feedback (battle_id, seq, tick, phase,
-- applied_seq, apply_errs); obs carries the components. Merged flat --
-- a component absent from obs is absent from the frame (partial frames
-- are the incremental-build reality; the host displays what is there).
function M.encode_obs(obs, header)
	local f = { v = spec.SPEC_VERSION };
	for k, v in pairs(header or {}) do f[k] = v; end;
	for k, v in pairs(obs or {}) do f[k] = v; end;
	return M.json(f);
end;

function M.encode_ack(last, ring)
	return nil, "todo(assemble + M.json)";
end;

-- Act text -> { seq, battle_id, for_tick, actions = [...] } | nil, err.
-- Validates the end-count and every verb against spec.VERBS; per-action
-- args named per ARG_TOKENS; SLOT indices +1'd into roster positions
-- here (vocab indices pass through 0-based -- see header). Unknown
-- verb = frame-level error (acked ERR, never silently skipped).
function M.parse_act(text)
	return nil, "todo(line loop via string.gmatch; tokenize; map args per spec.ARG_TOKENS)";
end;

return M;
