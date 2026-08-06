-------------------------------------------------------------------------
--	ml/game/ipc/channel.lua -- file transport (game side).
--
--	One-writer-per-file law: the game writes hello/obs/ack, the host
--	writes act. Nothing is written by both sides, ever.
--
--	Write atomicity (engine reality): os.rename cannot replace an
--	existing file on Windows, so every game-side write is tmp -> remove
--	-> rename with a brief NO-FILE window. That gap is part of the
--	protocol: the host reader serves last-good on ENOENT (codified in
--	host/channel.py -- v1's cockpit blanked instead; fixed contract).
--
--	Read side: the act file is read whole, seq-gated (small monotonic
--	ints only -- float32 Lua numbers), battle_id-gated, and DELETED at
--	battle start so a previous session's final order can never replay
--	(v1 harness bug, fixed here by design).
-------------------------------------------------------------------------

local util = require "ml/util";

local M = {};

M.PATH = {
	hello = "data/aai_ml_hello.json",	-- game -> host, once per battle
	obs = "data/aai_ml_obs.json",		-- game -> host, per decision tick
	ack = "data/aai_ml_ack.json",		-- game -> host, act results ring
	act = "data/aai_ml_act.txt",		-- host -> game, atomic (os.replace)
};

local last_seq = -1;

-- Wipe inbound state for a new battle: delete any stale act file, reset
-- the seq gate to -1. Also blanks ack with an id-only stamp (so the host
-- reads "nothing applied yet", not the previous battle's ring).
-- battle_id is our own os.date stamp -- safe to splice into the literal.
function M.reset(battle_id)
	pcall(os.remove, M.PATH.act);
	last_seq = -1;
	M.write(M.PATH.ack,
		'{"battle_id":"' .. tostring(battle_id) .. '","last":null,"ring":[]}');
end;

-- Game-side file write: tmp -> remove -> rename (the documented gap).
function M.write(path, text)
	local tmp = path .. ".tmp";
	local f = io.open(tmp, "wb");
	if not f then return false; end;
	f:write(text);
	f:close();
	os.remove(path);
	local ok = os.rename(tmp, path);
	return ok and true or false;
end;

-- Poll the act file: returns raw text of a NEW frame exactly once
-- (seq > last_seq and battle_id matches), else nil. Caller parses; a
-- parse error still consumes the seq (ERR-acked, never re-parsed).
function M.poll_act(battle_id)
	return nil, "todo(read whole file pcall'd; extract seq+id from line 1 cheaply; gate; advance last_seq)";
end;

return M;
