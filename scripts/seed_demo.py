"""Load the recorded demo snapshot into the local store.

    python scripts/seed_demo.py            # refuses to overwrite
    python scripts/seed_demo.py --force    # replace an existing local store

Then start the API with `PERSISTENCE=local` (the default in `.env.example`) and
open the run list: the recorded runs are there with the timestamps they were
executed at. `Reverify now` replays them from the recording, which is why the
snapshot ships the model calls and the search cache and not the L2/L5 layers.

Stop the API before seeding: the local store is a JSON file that the running
process holds in memory and rewrites on every change, so a copy underneath it
would be silently overwritten.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = REPO / "demo" / "state.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--store", default=str(REPO / ".tev"),
                        help="local store directory (matches TEV_LOCAL_DIR)")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing state.json")
    args = parser.parse_args()

    snapshot = Path(args.snapshot)
    if not snapshot.is_file():
        print(f"no snapshot at {snapshot}", file=sys.stderr)
        return 2
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{snapshot} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    meta = data.get("demo_meta") or {}
    runs = data.get("runs") or []
    if not runs:
        print(f"{snapshot} contains no runs; it is not a demo snapshot",
              file=sys.stderr)
        return 2

    store = Path(args.store)
    if not store.is_absolute():
        store = REPO / store
    target = store / "state.json"
    if target.is_file() and not args.force:
        print(f"{target} already exists. Move it aside or pass --force.",
              file=sys.stderr)
        return 1

    store.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, target)

    print(f"seeded {target} from {snapshot.name}")
    print(f"frozen at: {meta.get('frozen_at', 'unknown')}")
    print(f"runs: {len(runs)}")
    for run in runs:
        print(f"  {run.get('id')}  {run.get('status'):<10} {run.get('started_at')}")
    print("\nThese runs are a snapshot: their timestamps are the ones recorded, "
          "not now.")
    print("Start the API and open the run list. To reverify, use "
          "CACHE_MODE=read-only so a missing recording fails loudly instead of "
          "silently calling a provider.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
