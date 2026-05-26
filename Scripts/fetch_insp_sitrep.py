#!/usr/bin/env python3
"""
Standalone fetcher for INSP-RDC Ebola sitreps.

Downloads a sitrep PDF from a public INSP page, parses the cumulative per-
zone case table, and writes a CSV in the same schema as data/primary/sit_reps/
in the upstream repo. CSV columns:

    Country , Province, Health Zone, suspected cases, suspected deaths,
    confirmed cases, confirmed deaths

Dependencies:
    - Python 3.10+
    - pypdf
    - pandas
    All else is standard library.

This script is self-contained. The expected layout is:

    <public_data_root>/
      Scripts/
        fetch_insp_sitrep.py   ← this file
        backfill_insp_sitreps.py
        merge_sitrep_into_metadata.py
      Data/
        health_zone_metadata.csv
        Epidemiological Data/
          2026-05-XX.csv ...
          pdfs/

By default the parser writes CSVs to ../Data/Epidemiological Data/ and
saves the raw PDF into pdfs/ underneath that. Override with --out-dir.

Usage:
    python fetch_insp_sitrep.py
    python fetch_insp_sitrep.py --url https://insp.cd/sitrep-mve-n-011-2026/
    python fetch_insp_sitrep.py --pdf /path/to/local.pdf

The cumulative per-zone table was labeled "Tableau IV" in sitreps N°006-009
and "Tableau II" in N°010+. The parser anchors on the heading text
"Répartition des cas, décès suspects et confirmés" so it survives future
renumberings.
"""

from __future__ import annotations

import argparse
import io
import re
import urllib.error
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher  # noqa: F401  (reserved for future fuzzy zone matching)
from pathlib import Path

import pandas as pd
import pypdf

# ---------------------------------------------------------------------------
# Configuration constants — edit if INSP changes its sitrep layout.
# ---------------------------------------------------------------------------

DEFAULT_URL = "https://insp.cd/sitrep-mve-n-010-2026/"

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# INSP sitrep zone-name → canonical name used in our embeddings/shapefile.
# Add new entries here whenever a sitrep introduces a typo or naming variant.
ZONE_RENAMES: dict[str, str] = {
    "Kilo Mission": "Kilo",           # 'Mission' is a settlement suffix; ZS is Kilo
    "Miti Murhesa": "Miti-Murhesa",   # canonical has a hyphen
    "Rwmapara": "Rwampara",           # typo in early sitreps
    "Mungbwalu": "Mongbwalu",         # narrative-text variant on page 1 of N°007
    "Karissibi": "Karisimbi",         # typo in N°010 (correct: Karisimbi)
}

# French month → 2-digit number (for "21 mai 2026" style date parsing).
FR_MONTHS = {
    "janvier": "01", "janv": "01", "fevrier": "02", "février": "02", "févr": "02",
    "mars": "03", "avril": "04", "avri": "04", "mai": "05",
    "juin": "06", "juillet": "07", "juill": "07", "août": "08", "aout": "08",
    "septembre": "09", "sept": "09", "octobre": "10", "oct": "10",
    "novembre": "11", "nov": "11", "décembre": "12", "decembre": "12", "déc": "12",
}

# Tolerant int-or-ND parser fragment for use in re.compile patterns
_NUM = r"(\d+|ND|nd|\-)"

PROVINCE_PATTERNS = {
    "Ituri": r"\bItur\w*\b",
    "Nord-Kivu": r"\bNord[\s\-]?Kivu\b",
    "Sud-Kivu": r"\bSud[\s\-]?Kivu\b",
}


# ---------------------------------------------------------------------------
# HTTP / PDF helpers
# ---------------------------------------------------------------------------

def _request(url: str) -> bytes:
    """GET a URL with browser-spoofed headers. Percent-encodes non-ASCII
    characters (some INSP PDF filenames contain 'N°')."""
    from urllib.parse import urlsplit, urlunsplit, quote
    parts = urlsplit(url)
    safe_path = quote(parts.path, safe="/")
    safe_query = quote(parts.query, safe="=&")
    url = urlunsplit((parts.scheme, parts.netloc, safe_path, safe_query, parts.fragment))
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def discover_pdf_url(page_url: str) -> str:
    """Find the first .pdf link under wp-content/uploads on the sitrep page."""
    html = _request(page_url).decode("utf-8", errors="replace")
    candidates = re.findall(r'href="(https?://[^"]+?\.pdf)"', html, re.I)
    same_site = [c for c in candidates if "wp-content/uploads" in c]
    chosen = same_site[0] if same_site else (candidates[0] if candidates else "")
    if not chosen:
        raise RuntimeError(f"No PDF link found on {page_url}")
    return chosen


def fetch_pdf(pdf_url: str) -> bytes:
    print(f"downloading {pdf_url} …")
    data = _request(pdf_url)
    print(f"  {len(data):,} bytes")
    return data


def extract_text(pdf_bytes: bytes) -> list[str]:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return [(p.extract_text() or "") for p in reader.pages]


# ---------------------------------------------------------------------------
# Sitrep field parsers
# ---------------------------------------------------------------------------

def parse_report_date(pages: list[str]) -> str:
    """Pull 'Date de rapportage' (preferred) or 'Date de publication'.
    Returns YYYY-MM-DD."""
    text = "\n".join(pages)
    m = re.search(
        r"Date de rapportage\s*[:\-]?\s*(\d{1,2})\s+(\w+)\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"Date de publication\s*[:\-]?\s*(\d{1,2})\s+(\w+)\s+(\d{4})",
            text, re.IGNORECASE,
        )
    if not m:
        raise RuntimeError("Could not find Date de rapportage/publication in PDF")
    day = m.group(1).zfill(2)
    month_word = m.group(2).lower().rstrip(".")
    year = m.group(3)
    mm = FR_MONTHS.get(month_word) or FR_MONTHS.get(month_word[:3])
    if mm is None:
        raise RuntimeError(f"Unknown French month: {month_word!r}")
    return f"{year}-{mm}-{day}"


def _find_cumulative_table_block(pages: list[str]) -> str | None:
    """Locate the cumulative per-zone "Répartition des cas, décès suspects et
    confirmés" table block. INSP relabeled it across versions: Tableau IV in
    N°007-009, Tableau II in N°010+. We anchor on heading-text so future
    renumberings still parse. Returns the block from the heading to the next
    'Tableau' marker (or EOF), or None if not found."""
    full = "\n".join(pages)
    pat = (r"Tableau\s+\S+\.\s+R[ée]partition\s+des\s+cas,?\s+d[ée]c[èe]s\s+"
           r"suspects.*?(?=Tableau\s+\S+\.|$)")
    m = re.search(pat, full, re.IGNORECASE | re.DOTALL)
    return m.group(0) if m else None


def parse_table_total(pages: list[str]) -> dict | None:
    """Pull the 'Total' row from the cumulative per-zone table.
    Columns: suspected_cases, suspected_deaths, confirmed_cases, contacts.
    Returns None if not found. This row is the sitrep's authoritative
    national aggregate (per-zone sums may differ by a few cases because of
    unassigned 'Echantillons sans fiche' or PDF rounding)."""
    block = _find_cumulative_table_block(pages)
    if block is None:
        return None
    for line in block.splitlines():
        ls = line.strip()
        tm = re.match(
            r"^Total\s+" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM + r"\s*$",
            ls, re.IGNORECASE,
        )
        if tm:
            def asint(v):
                return v if v.upper() == "ND" or v == "-" else int(v)
            return {
                "suspected_cases":  asint(tm.group(1)),
                "suspected_deaths": asint(tm.group(2)),
                "confirmed_cases":  asint(tm.group(3)),
                "contacts":         asint(tm.group(4)),
            }
    return None


def parse_zone_rows(pages: list[str]) -> list[dict]:
    """Find the cumulative per-zone table and extract one row per zone.
    Returns list of dicts with country/province/zone/counts/contacts."""
    block = _find_cumulative_table_block(pages)
    if block is None:
        raise RuntimeError("Could not find cumulative per-zone table in PDF")

    rows = []
    current_province = None
    row_re = re.compile(
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\.\-\s]{1,40}?)\s+"
        + _NUM + r"\s+" + _NUM + r"\s+" + _NUM + r"\s+" + _NUM
    )

    for line in block.splitlines():
        ls = line.strip()
        if not ls:
            continue
        if re.search(r"^Tableau\s+\S+\.", ls, re.IGNORECASE):
            continue
        # province header detection (full row may also start with province)
        for prov, pat in PROVINCE_PATTERNS.items():
            if re.search(pat, ls, re.IGNORECASE):
                current_province = prov
                ls = re.sub(pat, "", ls, flags=re.IGNORECASE).strip()
                break
        # Filter non-zone rows: PDF text-extraction sometimes splits the
        # "Echantillons sans fiche" line so the per-zone parser sees just
        # "sans fiche" / "Echantillons" on its own. Match either form.
        if re.match(r"^(Total\b|Echantillons\b|sans fiche\b|\*)", ls, re.IGNORECASE):
            continue
        m = row_re.search(ls)
        if not m:
            continue
        zone = m.group(1).strip().rstrip(".").strip()
        if len(zone) < 3 or re.match(r"^(Nbre|Provinces|Zones)$", zone, re.IGNORECASE):
            continue
        if any(re.search(pat, zone, re.IGNORECASE) for pat in PROVINCE_PATTERNS.values()):
            continue
        zone = ZONE_RENAMES.get(zone, zone)
        def asint(v):
            return v if v.upper() == "ND" or v == "-" else int(v)
        rows.append({
            "country": "DRC",
            "province": current_province or "?",
            "zone": zone,
            "suspected_cases":  asint(m.group(2)),
            "suspected_deaths": asint(m.group(3)),
            "confirmed_cases":  asint(m.group(4)),
            "contacts":         asint(m.group(5)),
        })
    return rows


def parse_drc_total_confirmed_deaths(pages: list[str]) -> int | None:
    """Pull the DRC-wide 'Cumul décès parmi les confirmés' total from
    the page-1 summary box. Returns None if not found."""
    full = "\n".join(pages)
    m = re.search(r"(\d+)\s*\n?\s*Cumul\s+d[ée]c[èe]s\s+parmi\s+les\s+confirm",
                  full, re.IGNORECASE)
    if not m:
        m = re.search(r"Cumul\s+d[ée]c[èe]s\s+parmi\s+les\s+confirm[ée]s\s*:?\s*(\d+)",
                      full, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def update_latest_pointer(json_path: Path, *, url: str, pdf_url: str,
                          report_date: str, sitrep_number: int | None) -> None:
    """Maintain a small JSON sidecar pointing at the most-recent sitrep
    page+PDF the parser has seen. Used by the dashboard builder to embed
    the live link. Older sitreps will NOT overwrite a newer entry — the
    `report_date` field acts as the freshness key.

    The JSON has these fields:
        url             — canonical sitrep page URL (https://insp.cd/...)
        pdf_url         — direct PDF URL within that page
        sitrep_number   — INSP sitrep number (e.g. 10), or null if unknown
        report_date     — YYYY-MM-DD (Date de rapportage from the PDF)
        fetched_at      — ISO 8601 timestamp of last update
    """
    import json
    from datetime import datetime, timezone
    payload = {
        "url": url,
        "pdf_url": pdf_url,
        "sitrep_number": sitrep_number,
        "report_date": report_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if json_path.exists():
        try:
            existing = json.loads(json_path.read_text())
            if str(existing.get("report_date", "")) > report_date:
                print(f"  latest_sitrep.json already points at a newer "
                      f"report ({existing['report_date']} > {report_date}); "
                      f"not overwriting.")
                return
        except Exception:
            pass  # corrupt or missing — overwrite
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"  updated {json_path.name} -> N°{sitrep_number} / {report_date}")


def _extract_sitrep_number(url: str) -> int | None:
    """Pull the NNN out of a URL matching sitrep-mve-n-NNN-YYYY."""
    m = re.search(r"sitrep-mve-n-(\d{3})-\d{4}", url, re.IGNORECASE)
    return int(m.group(1)) if m else None


def write_csv(rows: list[dict], drc_conf_deaths: int | None,
              out_path: Path, table_total: dict | None = None) -> None:
    """Write the parsed sitrep to CSV. The Total row uses the PDF's
    authoritative cumulative-table Total row when present (passed as
    `table_total`); otherwise falls back to the per-zone sum. Per-zone rows
    are written verbatim from `rows`."""
    import csv
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if table_total is not None:
        total_susp   = table_total["suspected_cases"]
        total_susp_d = table_total["suspected_deaths"]
        total_conf   = table_total["confirmed_cases"]
    else:
        total_susp = sum(r["suspected_cases"] for r in rows
                         if isinstance(r["suspected_cases"], int))
        total_susp_d = sum(r["suspected_deaths"] for r in rows
                           if isinstance(r["suspected_deaths"], int))
        total_conf = sum(r["confirmed_cases"] for r in rows
                         if isinstance(r["confirmed_cases"], int))
    total_conf_d = drc_conf_deaths if drc_conf_deaths is not None else "ND"

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Country ", "Province", "Health Zone",
                    "suspected cases", "suspected deaths",
                    "confirmed cases", "confirmed deaths"])
        for r in rows:
            w.writerow([
                r["country"], r["province"], r["zone"],
                r["suspected_cases"], r["suspected_deaths"],
                r["confirmed_cases"], "ND",   # per-zone confirmed deaths not reported
            ])
        w.writerow(["Total", "", "", total_susp, total_susp_d, total_conf, total_conf_d])
    print(f"Wrote {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    here = Path(__file__).resolve().parent
    # public-data layout: scripts live in <root>/Scripts/, sitrep data in
    # <root>/Data/Epidemiological Data/.
    default_out = here.parent / "Data" / "Epidemiological Data"
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"Sitrep page URL (default: {DEFAULT_URL})")
    ap.add_argument("--pdf", type=Path, default=None,
                    help="Skip download; parse this local PDF file instead.")
    ap.add_argument("--out-dir", type=Path, default=default_out,
                    help="Where to write the dated CSV "
                         "(default: ../Data/Epidemiological Data/). "
                         "The raw PDF is saved into a pdfs/ subdir under this.")
    ap.add_argument("--out-csv", type=Path, default=None,
                    help="Override the dated CSV path entirely.")
    args = ap.parse_args()

    pdf_url: str | None = None  # set when we fetched from URL, used for latest-pointer
    if args.pdf is not None:
        pdf_bytes = args.pdf.read_bytes()
        print(f"reading {args.pdf} ({len(pdf_bytes):,} bytes)")
    else:
        pdf_url = discover_pdf_url(args.url)
        print(f"PDF: {pdf_url}")
        pdf_bytes = fetch_pdf(pdf_url)

    pages = extract_text(pdf_bytes)
    print(f"PDF pages: {len(pages)}")

    report_date = parse_report_date(pages)
    print(f"report date: {report_date}")

    rows = parse_zone_rows(pages)
    print(f"\nparsed {len(rows)} zone rows:")
    for r in rows:
        print(f"  {r['country']:<3} {r['province']:<10} {r['zone']:<20} "
              f"susp={r['suspected_cases']:>4}  suspD={r['suspected_deaths']:>3}  "
              f"conf={r['confirmed_cases']:>3}  contacts={r['contacts']}")

    drc_conf_deaths = parse_drc_total_confirmed_deaths(pages)
    print(f"\nDRC cumul. confirmed deaths (national total): {drc_conf_deaths}")

    table_total = parse_table_total(pages)
    if table_total is not None:
        print(f"Cumulative-table Total row (authoritative): "
              f"suspected={table_total['suspected_cases']}, "
              f"suspected_deaths={table_total['suspected_deaths']}, "
              f"confirmed={table_total['confirmed_cases']}, "
              f"contacts={table_total['contacts']}")

    # Save raw PDF next to the CSV (under <out-dir>/pdfs/) so we can re-parse
    # offline. Use the report-date-keyed filename for sorting.
    if args.pdf is None:
        pdf_dir = args.out_dir / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"SitRep_MVE_{report_date}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        print(f"Saved PDF -> {pdf_path}")

    out_path = args.out_csv or (args.out_dir / f"{report_date}.csv")
    write_csv(rows, drc_conf_deaths, out_path, table_total=table_total)

    # Update the latest-sitrep pointer for the dashboard builder. Only do
    # this when we have a URL (i.e. we fetched from the web, not parsed a
    # local --pdf). The function won't overwrite a JSON pointing at a
    # newer report_date than this one.
    if pdf_url is not None:
        latest_json = args.out_dir / "latest_sitrep.json"
        update_latest_pointer(
            latest_json,
            url=args.url,
            pdf_url=pdf_url,
            report_date=report_date,
            sitrep_number=_extract_sitrep_number(args.url),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
