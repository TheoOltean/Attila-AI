#!/usr/bin/env python3
"""Build attila_ai.pack (PFH4 mod pack) from pack_src/ and install it
into the game's data folder.

The pack only carries the two loader shims; the real scripts live loose in
data/script via the junction, so they can be edited without rebuilding.

Usage: python3 tools/build_pack.py
"""
import os
import struct
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_SRC = os.path.join(PROJECT, "pack_src")
GAME_DATA = "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
# sorts before tdd_pack0: among same-type packs, alphabetically first wins conflicts
PACK_NAME = "attila_ai.pack"

# Header layout observed in the game's own packs (data.pack, tdd_pack*):
# PFH4 | flags | dep-pack count | dep-pack index size | file count |
# file index size | timestamp. Mod packs use flags=3, index entries are
# u32 size + null-terminated windows-style path, data in index order.
FLAGS_MOD_PACK = 3
TIMESTAMP = 1751500000


def collect_files():
    entries = []
    for root, _, files in os.walk(PACK_SRC):
        for fname in sorted(files):
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, PACK_SRC).replace("/", "\\")
            with open(full, "rb") as f:
                entries.append((rel, f.read()))
    entries.sort(key=lambda e: e[0].lower())
    return entries


def build(entries, out_path):
    index = b"".join(
        struct.pack("<I", len(data)) + path.encode("latin-1") + b"\x00"
        for path, data in entries
    )
    header = struct.pack(
        "<4sIIIIII", b"PFH4", FLAGS_MOD_PACK, 0, 0, len(entries), len(index), TIMESTAMP
    )
    with open(out_path, "wb") as f:
        f.write(header)
        f.write(index)
        for _, data in entries:
            f.write(data)


def main():
    entries = collect_files()
    if not entries:
        sys.exit(f"no files found under {PACK_SRC}")
    out_path = os.path.join(GAME_DATA, PACK_NAME)
    build(entries, out_path)
    print(f"wrote {out_path} ({len(entries)} files):")
    for path, data in entries:
        print(f"  {len(data):>8} {path}")


if __name__ == "__main__":
    main()
