#!/usr/bin/env python3
"""
End-to-end refresh orchestrator for the public DRC Ebola dashboard.

Runs the three pipeline steps in order:

  1. Check INSP for new sitreps  (backfill_insp_sitreps.py, starting from
                                  one past the latest known sitrep number)
  2. Merge the newest sitrep into the per-zone metadata
                                 (merge_sitrep_into_metadata.py)
  3. Rebuild the dashboard       (build_dashboard_public.py)

Each step is run as a subprocess so the existing scripts are reused
without modification. The orchestrator is idempotent: if no new sitreps
are found in step 1, steps 2 and 3 are skipped automatically.

Expected layout (mirrors the other scripts in this folder):
    <public_data_root>/
      Scripts/
        refresh.py                       ← this file
        fetch_insp_sitrep.py
        backfill_insp_sitreps.py
        merge_sitrep_into_metadata.py
        build_dashboard_public.py
      Data/
        health_zone_metadata.csv
        Epidemiological Data/
          YYYY-MM-DD.csv ...
          pdfs/
          latest_sitrep.json

Run with no arguments:
    python refresh.py

Or skip individual steps:
    python refresh.py --skip-fetch    # don't check for new sitreps; just merge+rebuild
    python refresh.py --skip-rebuild  # check + merge, but don't touch the dashboard

The script exits 0 on success and prints a brief summary line. Dependencies
are exactly those of the underlying scripts (pypdf, pandas, plus whatever
build_dashboard_public.py uses).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_ROOT = HERE.parent / "Data"
SITREP_DIR = DATA_ROOT / "Epidemiological Data"
LATEST_JSON = SITREP_DIR / "latest_sitrep.json"
METADATA_CSV = DATA_ROOT / "health_zone_metadata.csv"

FETCH_SCRIPT = HERE / "fetch_insp_sitrep.py"
BACKFILL_SCRIPT = HERE / "backfill_insp_sitreps.py"
MERGE_SCRIPT = HERE / "merge_sitrep_into_metadata.py"
DASHBOARD_SCRIPT = HERE / "build_dashboard_public.py"


def _interpreter_has(py: str, modules: tuple[str, ...]) -> bool:
    """Return True iff `py -c 'import m1, m2, ...'` exits 0."""
    code = "import " + ", ".join(modules)
    try:
        return subprocess.run(
            [py, "-c", code],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, FileNotFoundError):
        return False


def pick_python(explicit: str | None) -> str:
    """Choose a Python interpreter that has the dependencies our subprocesses
    need (pandas + pypdf). Resolution order:

      1. --python flag (or REFRESH_PYTHON env var) — used verbatim.
      2. sys.executable — if it already has pandas + pypdf.
      3. A `.venv` / `venv` directory found by walking up from this script
         (up to 6 levels) — preferred so a user-created venv in the project
         root is auto-discovered.

    Bails out with a clear message if nothing works."""
    REQUIRED = ("pandas", "pypdf")

    if explicit:
        if not _interpreter_has(explicit, REQUIRED):
            sys.exit(f"--python {explicit} is missing one of {REQUIRED}; "
                     f"install with: {explicit} -m pip install pandas pypdf")
        return explicit

    env_py = os.environ.get("REFRESH_PYTHON")
    if env_py:
        if not _interpreter_has(env_py, REQUIRED):
            sys.exit(f"REFRESH_PYTHON={env_py} is missing one of {REQUIRED}.")
        return env_py

    if _interpreter_has(sys.executable, REQUIRED):
        return sys.executable

    # Walk up looking for a venv
    candidates: list[Path] = []
    cur = HERE
    for _ in range(7):
        for name in (".venv", "venv"):
            p = cur / name / "bin" / "python"
            if p.exists():
                candidates.append(p)
        if cur.parent == cur:
            break
        cur = cur.parent

    for cand in candidates:
        if _interpreter_has(str(cand), REQUIRED):
            print(f"(using interpreter: {cand})")
            return str(cand)

    sys.exit(
        f"\nNo Python interpreter with both pandas and pypdf was found.\n"
        f"  sys.executable = {sys.executable} (missing one of {REQUIRED})\n"
        f"  searched ancestor .venv/venv directories: "
        f"{[str(c) for c in candidates] or 'none'}\n"
        f"\nFix one of:\n"
        f"  • run with the right interpreter, e.g.  /path/to/.venv/bin/python {HERE.name}/refresh.py\n"
        f"  • set REFRESH_PYTHON=/path/to/python\n"
        f"  • pass --python /path/to/python\n"
        f"  • install deps:  {sys.executable} -m pip install pandas pypdf\n"
    )


def latest_known_sitrep_number() -> int:
    """Return the highest sitrep number we've successfully parsed before, or
    0 if no record exists. Sourced from latest_sitrep.json (maintained by
    fetch_insp_sitrep.update_latest_pointer)."""
    if not LATEST_JSON.exists():
        return 0
    try:
        n = json.loads(LATEST_JSON.read_text()).get("sitrep_number")
        return int(n) if isinstance(n, int) and n > 0 else 0
    except Exception:
        return 0


def run_step(cmd: list[str], label: str) -> None:
    """Run a subprocess with output streaming to this process's terminal.
    Aborts the whole orchestrator on non-zero exit."""
    print(f"\n========== {label} ==========")
    print("  $ " + " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(f"\n{label} FAILED with exit code {result.returncode}; aborting.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--skip-fetch", action="store_true",
                    help="Skip step 1 (don't check INSP for new sitreps).")
    ap.add_argument("--skip-merge", action="store_true",
                    help="Skip step 2 (don't merge into metadata).")
    ap.add_argument("--skip-rebuild", action="store_true",
                    help="Skip step 3 (don't rebuild the dashboard).")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="Run merge + dashboard rebuild even if no new sitreps.")
    ap.add_argument("--end", type=int, default=None,
                    help="Upper bound on the sitrep numbers to try (default: "
                         "start_n + 10).")
    ap.add_argument("--python", default=None,
                    help="Python interpreter to use for the child scripts "
                         "(default: sys.executable if it has pandas+pypdf, "
                         "else auto-discover .venv in an ancestor directory).")
    args = ap.parse_args()

    # Sanity-check that the sibling scripts exist
    for script in (FETCH_SCRIPT, BACKFILL_SCRIPT, MERGE_SCRIPT, DASHBOARD_SCRIPT):
        if not script.exists():
            sys.exit(f"missing required script: {script}")

    py = pick_python(args.python)
    n_known = latest_known_sitrep_number()
    print(f"Latest known sitrep on disk: N°{n_known:03d}" if n_known
          else "No latest_sitrep.json yet; will start at N°001")

    # Snapshot the state before fetch so we can tell if anything new arrived
    before_csvs = {p.name for p in SITREP_DIR.glob("????-??-??.csv")} if SITREP_DIR.exists() else set()
    md_before = METADATA_CSV.read_bytes() if METADATA_CSV.exists() else b""

    # ---------- Step 1: check for new sitreps ----------
    if args.skip_fetch:
        print("\n[--skip-fetch] skipping step 1.")
    else:
        start_n = max(1, n_known + 1)
        end_n = args.end or (start_n + 10)
        run_step([py, str(BACKFILL_SCRIPT),
                  "--start", str(start_n), "--end", str(end_n)],
                 f"Step 1/3: check INSP for new sitreps (N°{start_n:03d}..N°{end_n:03d})")

    # Detect whether new CSVs arrived
    after_csvs = {p.name for p in SITREP_DIR.glob("????-??-??.csv")} if SITREP_DIR.exists() else set()
    new_csvs = sorted(after_csvs - before_csvs)
    new_known = latest_known_sitrep_number()
    pointer_advanced = new_known > n_known
    nothing_changed = (not new_csvs) and (not pointer_advanced)

    if nothing_changed and not args.force_rebuild:
        print(f"\nNo new sitreps since N°{n_known:03d}. "
              f"(Use --force-rebuild to re-run merge + dashboard anyway.)")
        return 0

    if new_csvs:
        print(f"\nNew sitrep CSV(s) found: {new_csvs}")
    if pointer_advanced:
        print(f"latest_sitrep.json advanced: N°{n_known:03d} -> N°{new_known:03d}")

    # ---------- Step 2: merge latest sitrep into metadata ----------
    if args.skip_merge:
        print("\n[--skip-merge] skipping step 2.")
    else:
        run_step([py, str(MERGE_SCRIPT)],
                 "Step 2/3: merge latest sitrep into health_zone_metadata.csv")

    md_after = METADATA_CSV.read_bytes() if METADATA_CSV.exists() else b""
    md_changed = md_after != md_before
    if not md_changed and not args.force_rebuild and not args.skip_merge:
        print(f"\nMetadata unchanged after merge — sitrep contained no new "
              f"per-zone numbers. Skipping dashboard rebuild.")
        return 0

    # ---------- Step 3: rebuild dashboard ----------
    if args.skip_rebuild:
        print("\n[--skip-rebuild] skipping step 3.")
    else:
        run_step([py, str(DASHBOARD_SCRIPT)],
                 "Step 3/3: rebuild public dashboard")

    print(f"\n--- DONE ---  "
          f"Sitrep: N°{latest_known_sitrep_number():03d}  •  "
          f"Metadata: {'updated' if md_changed else 'unchanged'}  •  "
          f"Dashboard: {'rebuilt' if not args.skip_rebuild else 'skipped'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
