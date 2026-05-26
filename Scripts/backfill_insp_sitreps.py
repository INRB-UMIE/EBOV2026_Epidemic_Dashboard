#!/usr/bin/env python3
"""
Backfill all past INSP-RDC Ebola sitreps by iterating the predictable
URL pattern:

    https://insp.cd/sitrep-mve-n-NNN-YYYY/

where NNN is a zero-padded 3-digit number starting at 001 and YYYY is
the outbreak year (default 2026). Each sitrep page is processed through
the same logic as fetch_insp_sitrep.py — discover the linked PDF, parse
the cumulative per-zone table, and write <date>.csv.

Missing or malformed sitreps are skipped with a note (e.g. N°001 may
predate the per-zone case-table format). Existing files are NOT
overwritten unless --force is passed.

This script is self-contained and lives alongside the dated sitrep
CSVs. It imports fetch_insp_sitrep as a sibling module from the same
folder.

Usage:
    python backfill_insp_sitreps.py
    python backfill_insp_sitreps.py --start 1 --end 12 --year 2026
    python backfill_insp_sitreps.py --force
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
from pathlib import Path

# Make sibling fetch_insp_sitrep.py importable
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_insp_sitrep import (  # noqa: E402
    discover_pdf_url, fetch_pdf, extract_text,
    parse_report_date, parse_zone_rows, parse_table_total,
    parse_drc_total_confirmed_deaths, write_csv,
    update_latest_pointer,
)

URL_TEMPLATE = "https://insp.cd/sitrep-mve-n-{n:03d}-{year}/"


def process_one(n: int, year: int, force: bool, out_dir: Path) -> tuple[str, str]:
    """Returns (status, message) for the given sitrep number."""
    url = URL_TEMPLATE.format(n=n, year=year)
    try:
        pdf_url = discover_pdf_url(url)
    except urllib.error.HTTPError as e:
        return ("missing", f"  N°{n:03d}: page {url} -> HTTP {e.code}")
    except Exception as e:
        return ("error", f"  N°{n:03d}: discover_pdf_url failed: {e}")

    try:
        pdf_bytes = fetch_pdf(pdf_url)
        pages = extract_text(pdf_bytes)
    except Exception as e:
        return ("error", f"  N°{n:03d}: download/extract failed: {e}")

    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    try:
        report_date = parse_report_date(pages)
    except Exception as e:
        raw_path = pdf_dir / f"SitRep_MVE_N{n:03d}_{year}_unparseable.pdf"
        raw_path.write_bytes(pdf_bytes)
        return ("error",
                f"  N°{n:03d}: no parsable report date ({e}); raw PDF saved to {raw_path.name}")

    pdf_path = pdf_dir / f"SitRep_MVE_N{n:03d}_{report_date}.pdf"
    if not pdf_path.exists() or force:
        pdf_path.write_bytes(pdf_bytes)
        pdf_saved_msg = f"   saved PDF -> {pdf_path.name}"
    else:
        pdf_saved_msg = f"   PDF already on disk: {pdf_path.name}"

    out_path = out_dir / f"{report_date}.csv"
    csv_exists = out_path.exists()

    try:
        rows = parse_zone_rows(pages)
    except Exception:
        rows = []
    drc_conf_deaths = parse_drc_total_confirmed_deaths(pages)

    if not rows:
        return ("no-table",
                f"  N°{n:03d} ({report_date}): PDF saved; no per-zone cumulative "
                f"table found (early sitreps may not include it).\n{pdf_saved_msg}")

    if csv_exists and not force:
        # Even when skipping the CSV, advance the latest-sitrep pointer if
        # this is a newer report_date than what's on disk — so a re-run
        # without --force still keeps the pointer current.
        update_latest_pointer(out_dir / "latest_sitrep.json",
                              url=url, pdf_url=pdf_url,
                              report_date=report_date, sitrep_number=n)
        return ("skip",
                f"  N°{n:03d} ({report_date}): CSV already exists at {out_path.name}; "
                f"use --force to overwrite.\n{pdf_saved_msg}")
    write_csv(rows, drc_conf_deaths, out_path, table_total=parse_table_total(pages))
    update_latest_pointer(out_dir / "latest_sitrep.json",
                          url=url, pdf_url=pdf_url,
                          report_date=report_date, sitrep_number=n)
    return ("ok",
            f"  N°{n:03d} ({report_date}): wrote {len(rows)} zones -> {out_path.name}\n{pdf_saved_msg}")


def main() -> int:
    here = Path(__file__).resolve().parent
    default_out = here.parent / "Data" / "Epidemiological Data"
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--start", type=int, default=1, help="first N (default 1)")
    ap.add_argument("--end", type=int, default=20,
                    help="last N to try (default 20; stops earlier on consecutive 404s)")
    ap.add_argument("--year", type=int, default=2026, help="outbreak year (default 2026)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing CSV files")
    ap.add_argument("--max-misses", type=int, default=2,
                    help="stop after this many consecutive 404s (default 2)")
    ap.add_argument("--out-dir", type=Path, default=default_out,
                    help="Where to write CSVs and pdfs/ subfolder "
                         "(default: ../Data/Epidemiological Data/).")
    args = ap.parse_args()

    counts = {"ok": 0, "skip": 0, "missing": 0, "no-table": 0, "error": 0}
    consecutive_misses = 0
    for n in range(args.start, args.end + 1):
        status, msg = process_one(n, args.year, args.force, args.out_dir)
        counts[status] += 1
        print(msg)
        if status == "missing":
            consecutive_misses += 1
            if consecutive_misses >= args.max_misses:
                print(f"\nHit {consecutive_misses} consecutive missing sitreps; stopping at N={n}.")
                break
        else:
            consecutive_misses = 0

    print("\n--- summary ---")
    for k, v in counts.items():
        print(f"  {k:<10} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
