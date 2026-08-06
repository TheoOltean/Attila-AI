#!/usr/bin/env python3
"""Mirror src/*.lua into the game's data/aai_dev/ dev shadow.

The custom-battle kernel (src/aai_attach.lua) executes these copies with
stdio + loadstring and parks them in package.loaded, so they shadow the
pack for every module require -- edits land without a pack rebuild (and,
via the cockpit's "sync + reload" button, mid-battle). It NEVER touches
package.loaders: that crashed the game 3/3 at loading-screen end.

The KERNEL itself is exempt -- the engine loads it from the pack by name,
so kernel edits still need py tools/build_pack.py. The cockpit's /reload
route does this same copy itself; this CLI exists for syncing without the
viz running.

Usage: py tools/install_loose.py
"""
import os
import shutil

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PROJECT, "src")
DEV = os.path.join(
    r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila",
    "data", "aai_dev")


def main():
    copied = 0
    for root, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".lua"):
                continue
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, SRC)
            dst = os.path.join(DEV, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            print("  " + rel)
    print("synced %d files -> %s" % (copied, DEV))


if __name__ == "__main__":
    main()
