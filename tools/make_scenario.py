#!/usr/bin/env python3
"""Generate the menu-scenario data-door artifacts into pack_src/.

The experiment (branch custom-battles): the frontend's battle list is
DB-driven (db/battles_tables, 16 vanilla rows incl. hidden test rows), and
each scripted set-piece row points at a battle-setup XML whose
<battle_script> element names the Lua chunk the engine ATTACHES to the
battle -- the same privileged-environment mechanism our campaign
battle_entry.lua rides. If a mod pack can add rows here, we get
menu-launchable scripted battles with the full battle interface and NO
campaign. Formats + layout derivation: reference/CUSTOM_BATTLES.md.

Writes (all under pack_src/, committed as mod source):
  db/battles_tables/aai_battles    two new rows (new-file table merge):
                                     AAI_Adrianople -> vanilla AN_Battle.xml
                                       (pure menu-enumeration test)
                                     AAI_Scenario   -> our cloned XML with
                                       <battle_script> aai_start.lua
  text/db/battles.loc              vanilla file + our name/description
                                     entries (loc files replace whole-file,
                                     so vanilla content must be carried)
  script/aai_scenario/aai_battle.xml  AN_Battle.xml clone, battle_script
                                     element swapped to aai_start.lua

Usage: py tools/make_scenario.py   (reads the game's data.pack/local_en.pack)
"""
import os
import struct
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_SRC = os.path.join(PROJECT, "pack_src")
GAME_DATA = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data"
    if os.name == "nt"
    else "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
)

BATTLES_TABLE = "db\\battles_tables\\battles"
BATTLES_LOC = "text\\db\\battles.loc"
AN_XML = "script\\an_adrianople\\an_battle.xml"

ROWS = [
    # (key, type, spec, screenshot)
    ("AAI_Adrianople", "napoleon_historic",
     "Script/AN_Adrianople/AN_Battle.xml",
     "Script/AN_Adrianople/screenshot_small.png"),
    ("AAI_Scenario", "napoleon_historic",
     "Script/AAI_Scenario/AAI_Battle.xml",
     "Script/AN_Adrianople/screenshot_small.png"),
]
LOC_ADD = [
    ("battles_localised_name_AAI_Adrianople", "AAI Adrianople (row clone)"),
    ("battles_description_AAI_Adrianople",
     "Attila-AI menu-enumeration test: a duplicate battles-table row "
     "pointing at the vanilla Adrianople battle definition."),
    ("battles_localised_name_AAI_Scenario", "AAI Scenario"),
    ("battles_description_AAI_Scenario",
     "Attila-AI scripted scenario: Adrianople's armies and map with the "
     "battle script replaced by aai_start.lua (the menu battle door)."),
]


def pack_extract(pack_path, wanted):
    """Return the bytes of one file from a PFH4 pack (path match, any case)."""
    wanted = wanted.lower()
    with open(pack_path, "rb") as f:
        magic, flags, pf_count, pf_size, file_count, index_size, ts = \
            struct.unpack("<4sIIIIII", f.read(28))
        if magic != b"PFH4":
            raise SystemExit(f"unsupported pack magic {magic!r} in {pack_path}")
        f.read(pf_size)
        index = f.read(index_size)
        offset = f.tell()
        pos = 0
        for _ in range(file_count):
            size = struct.unpack_from("<I", index, pos)[0]
            pos += 4
            end = index.index(b"\x00", pos)
            path = index[pos:end].decode("latin-1")
            pos = end + 1
            if path.lower() == wanted:
                f.seek(offset)
                return f.read(size)
            offset += size
    raise SystemExit(f"{wanted} not found in {pack_path}")


def s8(text):
    b = text.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def read_s8(data, pos):
    ln = struct.unpack_from("<H", data, pos)[0]
    return data[pos + 2:pos + 2 + ln].decode("utf-8"), pos + 2 + ln


def parse_battles(data):
    """Parse the vanilla battles table; returns (version, rows).
    Row layout (derived 2026-08-03, version 6): key StringU8, type StringU8,
    1 boolean byte, spec StringU8, screenshot OptionalStringU8, 37-byte tail."""
    pos = 0
    if data[pos:pos + 4] == b"\xFD\xFE\xFC\xFF":  # GUID block
        pos += 4
        ln = struct.unpack_from("<H", data, pos)[0]
        pos += 2 + ln * 2
    if data[pos:pos + 4] != b"\xFC\xFD\xFE\xFF":
        raise SystemExit("battles table: no version marker")
    ver = struct.unpack_from("<I", data, pos + 4)[0]
    pos += 8
    pos += 1  # constant 0x01 byte before the row count
    count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    rows = []
    for _ in range(count):
        key, pos = read_s8(data, pos)
        btype, pos = read_s8(data, pos)
        boolean = data[pos]
        pos += 1
        spec, pos = read_s8(data, pos)
        shot = ""
        present = data[pos]
        pos += 1
        if present:
            shot, pos = read_s8(data, pos)
        tail = data[pos:pos + 37]
        pos += 37
        rows.append((key, btype, boolean, spec, shot, tail))
    if pos != len(data):
        raise SystemExit(f"battles table did not parse cleanly ({pos}/{len(data)})")
    return ver, rows


def build_battles_fragment(version, template_tail):
    body = b""
    for key, btype, spec, shot in ROWS:
        body += (s8(key) + s8(btype) + b"\x00" + s8(spec)
                 + b"\x01" + s8(shot) + template_tail)
    return (b"\xFC\xFD\xFE\xFF" + struct.pack("<I", version)
            + b"\x01" + struct.pack("<I", len(ROWS)) + body)


def amend_loc(data):
    """battles.loc + our entries. Format: FF FE 'LOC\\0' u16? u32 ver? --
    bytes 6..10 preserved verbatim -- u32 count, then rows of
    (u16 charlen, utf-16le) x2 + 1 tooltip-flag byte."""
    if data[:2] != b"\xff\xfe" or data[2:6] != b"LOC\x00":
        raise SystemExit(f"bad .loc header {data[:6]!r}")
    count = struct.unpack_from("<I", data, 10)[0]
    pos, raw_rows = 14, []
    for _ in range(count):
        start = pos
        for _ in range(2):
            ln = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + ln * 2
        flag = data[pos]
        pos += 1
        raw_rows.append(data[start:pos])
    if pos != len(data):
        raise SystemExit(f"battles.loc did not parse cleanly ({pos}/{len(data)})")
    flag_byte = raw_rows[0][-1:]

    def loc_str(text):
        b = text.encode("utf-16-le")
        return struct.pack("<H", len(b) // 2) + b

    for key, text in LOC_ADD:
        raw_rows.append(loc_str(key) + loc_str(text) + flag_byte)
    return (data[:10] + struct.pack("<I", len(raw_rows)) + b"".join(raw_rows))


def make_scenario_xml(an_xml_bytes):
    text = an_xml_bytes.decode("utf-16")
    old = '<battle_script prepare_for_fade_in="true">AN_Start.lua</battle_script>'
    new = '<battle_script prepare_for_fade_in="false">aai_start.lua</battle_script>'
    if text.count(old) != 1:
        raise SystemExit("battle_script element not found verbatim in AN_Battle.xml")
    return text.replace(old, new).encode("utf-16")  # utf-16 codec emits the BOM


def write(rel, data):
    dest = os.path.join(PACK_SRC, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print(f"  {len(data):>8} pack_src/{rel.replace(os.sep, '/')}")


def main():
    data_pack = os.path.join(GAME_DATA, "data.pack")
    local_pack = os.path.join(GAME_DATA, "local_en.pack")

    ver, rows = parse_battles(pack_extract(data_pack, BATTLES_TABLE))
    an = next(r for r in rows if r[0] == "Adrianople")
    print(f"vanilla battles table: version {ver}, {len(rows)} rows; "
          f"Adrianople tail {an[5].hex()}")

    print("writing:")
    write(os.path.join("db", "battles_tables", "aai_battles"),
          build_battles_fragment(ver, an[5]))
    write(os.path.join("text", "db", "battles.loc"),
          amend_loc(pack_extract(local_pack, BATTLES_LOC)))
    write(os.path.join("script", "aai_scenario", "aai_battle.xml"),
          make_scenario_xml(pack_extract(data_pack, AN_XML)))
    print("done -- rebuild the pack with: py tools/build_pack.py")


if __name__ == "__main__":
    main()
