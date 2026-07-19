#!/usr/bin/env python3
"""Compile aai_native.dll (32-bit, for the game's 32-bit process) and install it
into the game's data/ folder, where the battle Lua loads it via package.loadlib.

Requires the i686 mingw-w64 cross-compiler (WSL: gcc-mingw-w64-i686).
Usage: python3 native/build_native.py
"""
import os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DATA = "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
CC = "i686-w64-mingw32-gcc"
OBJDUMP = "i686-w64-mingw32-objdump"
SRC = os.path.join(HERE, "aai_native.c")
DEF = os.path.join(HERE, "aai_native.def")
OUT = os.path.join(HERE, "aai_native.dll")

def main():
    # -static: fold in libgcc etc. so the only runtime dep is msvcrt/kernel32
    cmd = [CC, "-shared", "-O2", "-s", "-static", "-o", OUT, SRC, DEF]
    print("compiling:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    try:
        out = subprocess.check_output([OBJDUMP, "-p", OUT], text=True)
        deps = [l.split()[-1] for l in out.splitlines() if "DLL Name" in l]
        print("runtime DLL deps:", ", ".join(deps) or "(none)")
    except Exception as e:
        print("objdump skipped:", e)
    dest = os.path.join(GAME_DATA, "aai_native.dll")
    shutil.copy2(OUT, dest)
    print("installed ->", dest)

if __name__ == "__main__":
    main()
