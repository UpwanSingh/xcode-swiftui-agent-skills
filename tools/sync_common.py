#!/usr/bin/env python3
"""Copy shared/_common.py into every skill's scripts/ directory (or verify the copies).

Skills must stay self-contained (a user may copy a single skill folder), so each one ships
its own copy of the helper module. This script keeps them identical.

  python3 tools/sync_common.py          # write copies
  python3 tools/sync_common.py --check  # exit 1 if any copy differs (used in CI)
"""
from __future__ import annotations

import argparse
import filecmp
import glob
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "shared", "_common.py")


def targets():
    return sorted(os.path.join(d, "_common.py") for d in glob.glob(os.path.join(ROOT, "skills", "*", "scripts")))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="only verify, do not write")
    args = p.parse_args(argv)
    stale = [t for t in targets() if not (os.path.exists(t) and filecmp.cmp(SOURCE, t, shallow=False))]
    if args.check:
        for t in stale:
            print(f"out of sync: {os.path.relpath(t, ROOT)}")
        if stale:
            print("run: python3 tools/sync_common.py")
        return 1 if stale else 0
    for t in stale:
        shutil.copyfile(SOURCE, t)
        print(f"updated {os.path.relpath(t, ROOT)}")
    print(f"{len(targets())} copies in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
