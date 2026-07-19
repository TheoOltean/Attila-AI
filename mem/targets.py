"""Machine-readable map of discovered engine addresses.

DORMANT / Tier-1 (2026-07-13): superseded for the BATTLE side by static RE in
../ghidra/findings/ (unit_field_map.md et al.). Only the campaign faction pointer
paths below (gold +0xDC, income +0xE4) remain uniquely durable here. See ../CLAUDE.md.

SOURCE OF TRUTH for the mem/ tools. Human-readable mirror + provenance (how each
field was identified, struct layouts): reference/STRUCTS.md.

Format: name -> (offset_path, type)
  offset_path : CE-style pointer path rooted at empire.retail.dll (see
                aai_mem.resolve): [base_off, o1, o2, ...] resolves to a live
                address every session, surviving restarts (module base is
                re-resolved live).
  type        : "i32" | "u32" | "float"

Group entries by struct so the layout is legible. Add a struct's fields as they
are mapped; keep reference/STRUCTS.md in sync.
"""

TARGETS = {
    # ---- CAMPAIGN: faction struct (anchored on gold/treasury) ----
    # DURABILITY: [0x21EFA14,0x0,0xDC] is [STABLE] -- verified across a restart
    # (Sassanid 6400) AND across a different campaign (Franci 4000). module+0x21EFA14
    # is a static ptr to "the current human faction"; gold at +0xDC. The struct at
    # (gold-0xDC) is the faction struct base -- reliable EVERY session.
    # The other 2 saved paths BROKE on restart; per-path stability, keep survivors only.
    # NOTE: tax_level / religion% are NOT in this struct (checked the whole block) --
    # they live elsewhere; map by change-detection (autonarrow), not gold-neighbors.
    "gold":      ([0x21EFA14, 0x0, 0xDC], "i32"),    # [STABLE] player treasury; faction base = gold-0xDC
    "income":    ([0x21EFA14, 0x0, 0xE4], "i32"),    # [V] faction balance/income (the number the UI shows);
                                                     # verified 2026-07-09 by predicting a -316 deficit turn.
                                                     # slightly approximate vs realized gold delta (end-turn events adjust it).

    # ---- BATTLE: unit struct ----
    # (to be filled by "reading battles end to end" — fingerprint men/ammo/pos)
}
