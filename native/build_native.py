#!/usr/bin/env python3
"""Compile aai_native.dll (32-bit, for the game's 32-bit process) and install it
into the game's data/ folder, where the battle Lua loads it via package.loadlib.

Requires the i686 mingw-w64 cross-compiler
(WSL: gcc-mingw-w64-i686; Windows: MSYS2 mingw-w64-i686-gcc, found via C:\\msys64).
Usage: python3 native/build_native.py
"""
import os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DATA = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data"
    if os.name == "nt"
    else "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
)
MSYS2_BIN = r"C:\msys64\mingw32\bin"

# The MSYS2 toolchain's subprocesses (cc1 etc.) need mingw32\bin on PATH for
# their runtime DLLs — without it they die silently with no diagnostics.
ENV = dict(os.environ)
if os.name == "nt" and os.path.isdir(MSYS2_BIN):
    ENV["PATH"] = MSYS2_BIN + os.pathsep + ENV["PATH"]

def find_tool(*names):
    """First of `names` found on PATH (with MSYS2 mingw32\\bin prepended on Windows)."""
    for n in names:
        hit = shutil.which(n, path=ENV["PATH"])
        if hit:
            return hit
    return names[0]  # let subprocess raise a clear error

CC = find_tool("i686-w64-mingw32-gcc")
OBJDUMP = find_tool("i686-w64-mingw32-objdump", "objdump")
SRC = os.path.join(HERE, "aai_native.c")
DEF = os.path.join(HERE, "aai_native.def")
OUT = os.path.join(HERE, "aai_native.dll")

def main():
    # -static: fold in libgcc etc. so the only runtime dep is msvcrt/kernel32
    cmd = [CC, "-shared", "-O2", "-s", "-static", "-o", OUT, SRC, DEF]
    print("compiling:", " ".join(cmd))
    subprocess.run(cmd, check=True, env=ENV)
    try:
        out = subprocess.check_output([OBJDUMP, "-p", OUT], text=True, env=ENV)
        deps = [l.split()[-1] for l in out.splitlines() if "DLL Name" in l]
        print("runtime DLL deps:", ", ".join(deps) or "(none)")
        # Print the export table too: a stale install is otherwise invisible
        # until a Lua module quietly fails to resolve its symbol.
        exports, seen = [], False
        for line in out.splitlines():
            if "Ordinal/Name Pointer" in line:
                seen = True
                continue
            if seen:
                parts = line.split()
                if len(parts) == 2 and parts[0].startswith("["):
                    exports.append(parts[1])
                elif line.strip() == "" and exports:
                    break
        print("exports:", ", ".join(exports) or "(none parsed)")
    except Exception as e:
        print("objdump skipped:", e)
    dest = os.path.join(GAME_DATA, "aai_native.dll")
    shutil.copy2(OUT, dest)
    print("installed ->", dest)

if __name__ == "__main__":
    main()
