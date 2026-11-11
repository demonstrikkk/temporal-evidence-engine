"""Run all five WP-4 evaluation suites and report each separately.

Suites are never blended into one score. Each suite prints its own table and
its own PASS/FAIL gate.

Run from the repo root:

    python eval/run_all_suites.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.extraction_suite import main as extraction_main  # noqa: E402
from eval.research_suite import main as research_main  # noqa: E402
from eval.answer_quality_suite import main as answer_quality_main  # noqa: E402
from eval.efficiency_suite import main as efficiency_main  # noqa: E402

SUITES = [
    ("Suite 1 — Extraction", extraction_main),
    ("Suite 2 — Research", research_main),
    # Suite 3 — Verification is the existing hermetic gold gate (eval/run_eval.py).
    ("Suite 4 — Answer Quality", answer_quality_main),
    ("Suite 5 — Efficiency", efficiency_main),
]


def main() -> int:
    print("# WP-4 — Five evaluation suites\n")
    print("Each suite is reported separately. They are never blended into one score.\n")
    overall_ok = True
    for name, main_fn in SUITES:
        print(f"## {name}\n")
        rc = main_fn()
        overall_ok = overall_ok and rc == 0
        print()
    print("# Summary")
    for name, _ in SUITES:
        print(f"- {name}: reported above")
    print()
    if overall_ok:
        print("All suites ran. Check each suite's PASS/FAIL gate above.")
    else:
        print("One or more suites returned a non-zero exit code.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
