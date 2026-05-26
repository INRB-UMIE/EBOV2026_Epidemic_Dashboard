#!/usr/bin/env python3
"""
Merge the latest dated sitrep CSV (in this folder) into the per-zone
metadata file used by the public dashboard. Only the four case/death
columns are touched:

    suspected_cases, suspected_deaths, confirmed_cases, confirmed_deaths

Every other column in health_zone_metadata.csv (population, geometry,
mobility totals, projected_*, ...) is preserved untouched.

Expected layout:
    <public_data_root>/
      Scripts/
        merge_sitrep_into_metadata.py   ← this file
        fetch_insp_sitrep.py
      Data/
        health_zone_metadata.csv
        Epidemiological Data/
          YYYY-MM-DD.csv ...

By default:
  - reads ../Data/health_zone_metadata.csv
  - picks the most recent YYYY-MM-DD.csv in ../Data/Epidemiological Data/
  - writes the updated metadata back over the same file

Override with --metadata, --sitrep, --out.

The matching key is the lowercased Health Zone name. Zone-name aliases
known to appear in INSP sitreps are normalized via the same renames
defined in fetch_insp_sitrep.py.

Dependencies: pandas only.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
# Public-data layout: <root>/Scripts/, <root>/Data/, <root>/Data/Epidemiological Data/.
SITREP_DIR = HERE.parent / "Data" / "Epidemiological Data"
METADATA_DEFAULT = HERE.parent / "Data" / "health_zone_metadata.csv"

sys.path.insert(0, str(HERE))
from fetch_insp_sitrep import ZONE_RENAMES  # noqa: E402


def find_latest_sitrep(sitrep_dir: Path) -> Path | None:
    """Return the most recent YYYY-MM-DD.csv in sitrep_dir, or None."""
    dated = []
    for p in sitrep_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".csv":
            continue
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        dated.append((d, p))
    if not dated:
        return None
    return max(dated)[1]


def load_sitrep(path: Path) -> tuple[dict[str, dict[str, int]], dict[str, int] | None]:
    """Returns (per-zone lookup, Total-row dict or None).

    The per-zone lookup is keyed on lowercased zone name. 'ND' / missing
    values are coerced to 0. The Total row (if present) is returned
    separately as a dict with the same four count fields plus a
    'national_confirmed_deaths' field (since per-zone confirmed_deaths
    is typically 'ND' in INSP sitreps and the only published figure is
    the national total)."""
    sr = pd.read_csv(path)
    sr.columns = [c.strip().lower() for c in sr.columns]

    total_mask = sr["country"].astype(str).str.strip().str.lower() == "total"
    total_row = sr[total_mask].iloc[0] if total_mask.any() else None
    sr = sr[~total_mask].copy()
    sr = sr[sr["country"].astype(str).str.strip().str.upper() == "DRC"].copy()
    sr["health zone"] = sr["health zone"].replace(ZONE_RENAMES)

    for c in ("suspected cases", "suspected deaths",
              "confirmed cases", "confirmed deaths"):
        sr[c] = pd.to_numeric(sr[c], errors="coerce").fillna(0).astype(int)

    lookup = {
        str(r["health zone"]).strip().lower(): {
            "suspected_cases":  int(r["suspected cases"]),
            "suspected_deaths": int(r["suspected deaths"]),
            "confirmed_cases":  int(r["confirmed cases"]),
            "confirmed_deaths": int(r["confirmed deaths"]),
        }
        for _, r in sr.iterrows()
    }

    total_dict = None
    if total_row is not None:
        def _to_int(v):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return 0
        total_dict = {
            "suspected_cases":  _to_int(total_row["suspected cases"]),
            "suspected_deaths": _to_int(total_row["suspected deaths"]),
            "confirmed_cases":  _to_int(total_row["confirmed cases"]),
            "national_confirmed_deaths": _to_int(total_row["confirmed deaths"]),
        }
    return lookup, total_dict


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--metadata", type=Path,
                    default=METADATA_DEFAULT,
                    help="path to health_zone_metadata.csv "
                         "(default: ../Data/health_zone_metadata.csv)")
    ap.add_argument("--sitrep", type=Path, default=None,
                    help="path to a specific sitrep CSV (default: most recent "
                         "YYYY-MM-DD.csv in ../Data/Epidemiological Data/)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output metadata path (default: overwrite --metadata)")
    args = ap.parse_args()

    if not args.metadata.exists():
        sys.exit(f"metadata not found: {args.metadata}")

    sitrep_path = args.sitrep or find_latest_sitrep(SITREP_DIR)
    if sitrep_path is None or not sitrep_path.exists():
        sys.exit(f"no dated sitrep CSV in {SITREP_DIR}")
    print(f"merging sitrep: {sitrep_path.name}")

    lookup, total = load_sitrep(sitrep_path)
    print(f"  {len(lookup)} DRC zones in sitrep")

    md = pd.read_csv(args.metadata)
    cols = ["suspected_cases", "suspected_deaths",
            "confirmed_cases", "confirmed_deaths"]
    matched = 0
    for col in cols:
        new_vals = []
        for nm in md["name"]:
            key = str(nm).strip().lower() if pd.notna(nm) else ""
            new_vals.append(int(lookup.get(key, {}).get(col, 0)))
        md[col] = new_vals
    # Count matched zones (DRC rows that appear in the lookup)
    norms = {str(n).strip().lower() for n in md["name"] if pd.notna(n)}
    matched = sum(1 for k in lookup if k in norms)
    print(f"  matched {matched}/{len(lookup)} sitrep zones to metadata "
          f"(non-matching zones: "
          f"{sorted(k for k in lookup if k not in norms)})")

    out_path = args.out or args.metadata
    md.to_csv(out_path, index=False)
    print(f"wrote {out_path}")

    if total is not None:
        print(f"\nNational totals from sitrep Total row "
              f"(authoritative; per-zone sum may differ slightly):")
        print(f"  suspected cases:    {total['suspected_cases']:>5}")
        print(f"  suspected deaths:   {total['suspected_deaths']:>5}")
        print(f"  confirmed cases:    {total['confirmed_cases']:>5}")
        print(f"  confirmed deaths:   {total['national_confirmed_deaths']:>5}  "
              f"(per-zone is 'ND' in INSP sitreps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
