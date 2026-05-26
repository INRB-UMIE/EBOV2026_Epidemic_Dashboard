#!/usr/bin/env python3
"""
Build the DRC Ebola Bundibugyo 2026 dashboard as a single self-contained
HTML file from publicly available inputs.

Usage
-----
    python build_dashboard.py

Layout
------
The script reads everything from a single ``Data/`` directory that lives at
the project root, alongside the ``Scripts/`` folder containing this file::

    project_root/
    ├── Scripts/
    │   └── build_dashboard_public.py    (this file)
    ├── Data/
    │   ├── health_zone_metadata.csv         per-zone metrics (one row per zone)
    │   ├── DRC Health Zones/<*.shp,*.dbf,*.shx,*.prj,...>
    │   │                                    OMS/DSNIS administrative boundaries
    │   ├── Epidemiological Data/<YYYY-MM-DD>.csv
    │   │                                    INSP situation-report CSVs (one per date;
    │   │                                    most recent is used for the header banner)
    │   ├── Methods/Contributors_Methods_Data_website.docx
    │   │                                    Contributors / Data / Methods text shown
    │   │                                    inside the "Contributors, Data, and Methods"
    │   │                                    modal. Hyperlinks and headings in the docx
    │   │                                    are preserved.
    │   ├── ToS/Terms of Use.txt             plain-text Terms of Use
    │   ├── Branding/                        partner logos + URLs map
    │   │   ├── urls.txt                     "<filename>, <https url>" per line
    │   │   ├── inrb.png
    │   │   ├── INSP.jpeg
    │   │   ├── INOHA.jpeg
    │   │   └── UMIE.jpeg
    │   └── Refugee_IDP sites/<*.geojson>    (optional; the dashboard exposes only
    │                                         per-zone aggregates, never coordinates)
    └── output/
        └── dashboard.html                   build artefact

Set the ``DATA_ROOT`` environment variable to override the default Data/
location (useful when testing from a different working directory).

Inputs are tolerated when missing: the corresponding layers / sections are
hidden and a warning is printed.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import mapping, shape
from shapely.validation import make_valid


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("DATA_ROOT") or (SCRIPT_DIR.parent / "Data")).resolve()
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
OUTPUT_PATH = OUTPUT_DIR / "dashboard.html"

BUILD_DIR = Path(os.environ.get("BUILD_DIR") or
                 (SCRIPT_DIR.parent.parent / "Ebola_DRC_2026" / "build")).resolve()
BUILD_GEOJSON    = BUILD_DIR / "drc_health_zones.geojson"
BUILD_LONG_DIR   = BUILD_DIR / "long"
EXTERNAL_DATA    = BUILD_DIR.parent / "data"

METADATA_CSV     = DATA_ROOT / "health_zone_metadata.csv"
SIT_REPS_DIR     = DATA_ROOT / "Epidemiological Data"
METHODS_DOCX     = DATA_ROOT / "Methods" / "Contributors_Methods_Data_website.docx"
TERMS_TXT        = DATA_ROOT / "ToS" / "Terms of Use.txt"
BRANDING_DIR     = DATA_ROOT / "Branding"
BRANDING_URLS    = BRANDING_DIR / "urls.txt"


# ---------------------------------------------------------------------------
# visual constants
# ---------------------------------------------------------------------------

SIMPLIFY_TOL = 0.001     # ~110 m at the equator; ~10× fewer vertices than raw
COORD_DECIMALS = 5
TRAVEL_FROM_ZONE = "Mongbalu"
ASOF_FALLBACK = ""
INSP_FALLBACK_URL = "https://insp.cd/"
LATEST_SITREP_JSON = SIT_REPS_DIR / "latest_sitrep.json"

# Maps metadata CSV names → build GeoJSON nom values where they differ.
_NAME_TO_NOM = {
    "Banzow Moke": "Banjow Moke",
    "Bogosenubea": "Bogosenubia",
    "Busanga": "Bosanga",
    "Citenge": "Tshitenge",
    "Gety": "Gethy",
    "Gungu (Secteur)": "Gungu",
    "Idiofa (Secteur)": "Idiofa",
    "Kabeya Kamwanga": "Kabeya Kamuanga",
    "Kabondo-Dianda": "Kabondo Dianda",
    "Kasongo-Lunda": "Kasongo Lunda",
    "Kiambi": "Kiyambi",
    "Kimbao": "Kimbau",
    "Lubunga": "Lubunga (Tshopo)",
    "Malemba Nkulu": "Malemba",
    "Mongbwalu": "Mongbalu",
    "Nia Nia": "Nia-Nia",
    "Nsona-Pangu": "Nsona-Mpangu",
    "Nyankunde": "Nyakunde",
    "Nyirangongo": "Nyiragongo",
    "Pendjua": "Penjwa",
    "Yalifafu": "Yalifafo",
}
_NOM_TO_NAME = {v: k for k, v in _NAME_TO_NOM.items()}

PARTNER_ORDER = ["INSP.png", "inrb.png", "INOHA.jpeg", "UMIE.jpeg", "africa-cdc.png"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def detect_asof() -> str:
    """Derive the 'latest case report' date.
    Checks local sit-rep CSVs first, then falls back to _date fields in the
    build GeoJSON."""
    if SIT_REPS_DIR.exists():
        dated = []
        for p in SIT_REPS_DIR.iterdir():
            if not p.is_file() or p.suffix.lower() != ".csv":
                continue
            try:
                dated.append((datetime.strptime(p.stem, "%Y-%m-%d").date(), p))
            except ValueError:
                continue
        if dated:
            d, _ = max(dated)
            return d.strftime("%d %b %Y").lstrip("0")
    if BUILD_GEOJSON.exists():
        with open(BUILD_GEOJSON) as f:
            raw = json.load(f)
        dates = set()
        for feat in raw["features"]:
            for src in (feat["properties"].get("insp_sitrep", {}),
                        feat["properties"].get("epi", {})):
                for v in src.values():
                    if isinstance(v, dict) and "_date" in v:
                        try:
                            dates.add(datetime.strptime(v["_date"], "%Y-%m-%d").date())
                        except (ValueError, TypeError):
                            pass
        if dates:
            return max(dates).strftime("%d %b %Y").lstrip("0")
    return ASOF_FALLBACK


def latest_insp_url() -> str:
    """Return the most-recent INSP sitrep page URL written by
    fetch_insp_sitrep.update_latest_pointer. Falls back to the INSP root
    if the pointer file is missing or unreadable."""
    if not LATEST_SITREP_JSON.exists():
        return INSP_FALLBACK_URL
    try:
        url = json.loads(LATEST_SITREP_JSON.read_text()).get("url")
    except Exception:
        return INSP_FALLBACK_URL
    return url if isinstance(url, str) and url else INSP_FALLBACK_URL


_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s) -> str:
    return _NORM_RE.sub("", str(s).lower()) if s else ""


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(v) else v


def _i(x):
    v = _f(x)
    return None if v is None else int(round(v))


def _round_coords(geom_dict: dict, ndigits: int) -> dict:
    def _walk(o):
        if isinstance(o, (list, tuple)):
            if o and isinstance(o[0], (int, float)):
                return [round(float(c), ndigits) for c in o]
            return [_walk(x) for x in o]
        return o

    g = dict(geom_dict)
    g["coordinates"] = _walk(g.get("coordinates"))
    return g


# ---------------------------------------------------------------------------
# geometry: read DRC health-zone polygons, match per-zone metadata rows
# ---------------------------------------------------------------------------

def load_features_from_geojson() -> tuple[list[dict], dict[str, tuple[float, float]]]:
    """Load zone polygons from the build GeoJSON, keyed by nom."""
    with open(BUILD_GEOJSON) as f:
        raw = json.load(f)

    feats: list[dict] = []
    centroids: dict[str, tuple[float, float]] = {}
    for feat in raw["features"]:
        nom = feat["properties"]["nom"]
        geom = make_valid(shape(feat["geometry"]))
        if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"}:
            continue
        orig_centroid = geom.centroid
        if SIMPLIFY_TOL > 0:
            geom = geom.simplify(SIMPLIFY_TOL, preserve_topology=True)
        if geom.is_empty:
            continue
        gdict = mapping(geom)
        if COORD_DECIMALS is not None:
            gdict = _round_coords(gdict, COORD_DECIMALS)
        feats.append({
            "type": "Feature",
            "geometry": gdict,
            "properties": {"nom": nom, "name": nom},
        })
        centroids[nom] = (float(orig_centroid.x), float(orig_centroid.y))
    return feats, centroids


def _load_build_geojson_properties() -> dict[str, dict]:
    """Return {nom: properties_dict} from the build GeoJSON."""
    with open(BUILD_GEOJSON) as f:
        raw = json.load(f)
    return {feat["properties"]["nom"]: feat["properties"]
            for feat in raw["features"]}


# ---------------------------------------------------------------------------
# per-zone payload
# ---------------------------------------------------------------------------

def _load_local_csv_fields() -> dict[str, dict]:
    """Load fields only available in the local metadata CSV (not in the build),
    keyed by nom (using _NAME_TO_NOM to translate CSV name → build nom)."""
    if not METADATA_CSV.exists():
        print(f"  WARNING: {METADATA_CSV} not found, local-only fields unavailable")
        return {}
    df = pd.read_csv(METADATA_CSV)
    df = df.dropna(subset=["ref_dhis2"]).copy()
    df["name"] = df["name"].astype(str)

    if "relative_risk" not in df.columns and "projected_true_infections" in df.columns:
        proj = pd.to_numeric(df["projected_true_infections"], errors="coerce")
        proj_max = proj.max()
        if pd.notna(proj_max) and proj_max > 0:
            df["relative_risk"] = np.log1p(proj.fillna(0)) / np.log1p(proj_max)
        else:
            df["relative_risk"] = np.nan

    fields_f = [
        "population_lower", "population_upper",
        "nearest_refugee_camp_km",
        "calibration_effective_distance_from_mongbwalu",
        "relative_risk",
    ]
    fields_i = [
        "n_refugee_camps_within_50km", "n_refugee_camps_within_100km",
        "n_pcr_tests_target",
    ]
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        name = row["name"]
        nom = _NAME_TO_NOM.get(name, name)
        rec: dict = {}
        for c in fields_f:
            if c in df.columns:
                rec[c] = _f(row.get(c))
        for c in fields_i:
            if c in df.columns:
                rec[c] = _i(row.get(c))
        out[nom] = rec
    return out


def _extract_matrix_column(csv_path: Path, target_col: str) -> dict[str, float | None]:
    """Extract a single named column from a zone-to-zone matrix CSV.
    Returns {nom: value}."""
    if not csv_path.exists():
        print(f"  WARNING: {csv_path} not found")
        return {}
    df = pd.read_csv(csv_path)
    nom_col = "nom" if "nom" in df.columns else df.columns[1]
    col = None
    target_dotted = target_col.replace(" ", ".")
    for c in df.columns:
        if c == target_col or c == target_dotted:
            col = c
            break
    if col is None:
        print(f"  WARNING: column {target_col!r} not found in {csv_path.name}")
        return {}
    return {str(row[nom_col]): _f(row[col]) for _, row in df.iterrows()}


def _extract_matrix_row_sums(csv_path: Path) -> dict[str, float | None]:
    """Sum each row of a zone-to-zone matrix (excluding the nom column).
    Returns {nom: row_sum}."""
    if not csv_path.exists():
        print(f"  WARNING: {csv_path} not found")
        return {}
    df = pd.read_csv(csv_path)
    if "nom" in df.columns:
        noms = df["nom"].astype(str)
        numeric = df.drop(columns=["nom"]).apply(pd.to_numeric, errors="coerce")
    else:
        noms = df.iloc[:, 1].astype(str)
        numeric = df.iloc[:, 2:].apply(pd.to_numeric, errors="coerce")
    sums = numeric.sum(axis=1)
    return {noms.iloc[i]: _f(sums.iloc[i]) for i in range(len(df))}


def load_metadata(
    centroids: dict[str, tuple[float, float]],
) -> tuple[dict[str, dict], dict]:
    """Assemble per-zone metadata from build GeoJSON properties, OSRM matrices,
    IDP/Flowminder matrices, and local CSV fallback fields."""
    build_props = _load_build_geojson_properties()
    local_fields = _load_local_csv_fields()

    # OSRM matrices
    travel_times = _extract_matrix_column(
        BUILD_LONG_DIR / "osrm__travel_time.csv", "Mongbalu")
    road_dists = _extract_matrix_column(
        BUILD_LONG_DIR / "osrm__road_distance.csv", "Mongbalu")

    # IDP and Flowminder matrices (row sums = incoming totals)
    idp_incoming = _extract_matrix_row_sums(
        EXTERNAL_DATA / "IDP" / "processed" / "idp__individuals__static.matrix.csv")
    flowminder_incoming = _extract_matrix_row_sums(
        EXTERNAL_DATA / "flowminder" / "processed" / "flowminder__inflow__static.matrix.csv")

    zone_data: dict[str, dict] = {}
    for nom, props in build_props.items():
        rec: dict = {"name": nom}

        # Centroids
        if nom in centroids:
            lon, lat = centroids[nom]
            rec["centroid_lon"] = lon
            rec["centroid_lat"] = lat

        # Population (worldpop)
        wp = props.get("worldpop", {})
        rec["population"] = _f(wp.get("pop_count", {}).get("pop_count"))

        # Health facilities (GRID3)
        g3 = props.get("grid3_healthsites", {})
        rec["n_health_facilities"] = _i(
            g3.get("healthsite_count", {}).get("healthsite_count"))

        # Epi: prefer INSP sitrep cumulative (more recent) over epi snapshot.
        # Use explicit None checks — `or` would treat 0 as falsy.
        insp = props.get("insp_sitrep", {})
        epi = props.get("epi", {}).get("cases", {})
        for dst, insp_key, epi_key in (
            ("confirmed_cases",  "cumulative_confirmed_cases",  "confirmed_cases"),
            ("confirmed_deaths", "cumulative_confirmed_deaths", "confirmed_deaths"),
            ("suspected_cases",  "cumulative_suspected_cases",  "suspected_cases"),
            ("suspected_deaths", "cumulative_suspected_deaths", "suspected_deaths"),
        ):
            v = _i(insp.get(insp_key, {}).get(insp_key))
            if v is None:
                v = _i(epi.get(epi_key))
            rec[dst] = v
        rec["total_cases"] = (rec.get("confirmed_cases") or 0) + (rec.get("suspected_cases") or 0)

        # Refugee/IDP site count
        rs = props.get("refugee_sites", {})
        rec["refugee_site_count"] = _i(
            rs.get("sites", {}).get("sites"))

        # OSRM (travel time is in minutes in the matrix → convert to hours)
        tt = travel_times.get(nom)
        rec["travel_time_to_mongbwalu_h"] = round(tt / 60, 2) if tt else None
        rec["geodesic_to_mongbwalu_km"] = road_dists.get(nom)

        # IDP / Flowminder
        rec["displaced_in_individuals_12mo"] = _i(idp_incoming.get(nom))
        rec["flowminder_in_mar2026"] = _i(flowminder_incoming.get(nom))

        # Local-CSV-only fields
        local = local_fields.get(nom, {})
        rec["population_lower"] = local.get("population_lower")
        rec["population_upper"] = local.get("population_upper")
        rec["nearest_refugee_camp_km"] = local.get("nearest_refugee_camp_km")
        rec["n_refugee_camps_within_50km"] = local.get("n_refugee_camps_within_50km")
        rec["n_refugee_camps_within_100km"] = local.get("n_refugee_camps_within_100km")
        rec["calibration_effective_distance_from_mongbwalu"] = local.get(
            "calibration_effective_distance_from_mongbwalu")
        rec["relative_risk"] = local.get("relative_risk")
        rec["n_pcr_tests_target"] = local.get("n_pcr_tests_target")

        zone_data[nom] = rec

    # Case totals
    totals: dict = {}
    for col in ("confirmed_cases", "confirmed_deaths",
                "suspected_cases", "suspected_deaths"):
        totals[col] = sum(int(r.get(col) or 0) for r in zone_data.values())
    totals["affected_zones"] = sum(
        1 for r in zone_data.values()
        if (int(r.get("confirmed_cases") or 0) + int(r.get("suspected_cases") or 0)) > 0)

    return zone_data, totals


def compute_global_sitrep_totals() -> dict:
    """Aggregate confirmed/suspected cases + deaths across all countries from the
    newest dated sit-rep CSV. Returns per-country breakdown plus global totals.

    When a sit-rep's Total row underreports relative to the sum of per-zone
    rows (e.g. INSP marks per-zone confirmed_deaths as "ND" and reports the
    national aggregate only on the Total row), we credit the missing count to
    the largest country and trust the higher number.
    """
    out = {
        "global_confirmed_cases": 0, "global_suspected_cases": 0,
        "global_confirmed_deaths": 0, "global_suspected_deaths": 0,
        "global_total_cases": 0,
        "affected_countries": [], "affected_country_count": 0,
        "per_country": [],
    }
    if not SIT_REPS_DIR.exists():
        return None
    dated = []
    for p in SIT_REPS_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != ".csv":
            continue
        try:
            dated.append((datetime.strptime(p.stem, "%Y-%m-%d").date(), p))
        except ValueError:
            continue
    if not dated:
        return None
    _, path = max(dated)
    sr_all = pd.read_csv(path)
    sr_all.columns = [c.strip().lower() for c in sr_all.columns]
    total_mask = sr_all["country"].astype(str).str.strip().str.lower() == "total"
    total_row = sr_all[total_mask].iloc[0] if total_mask.any() else None
    sr = sr_all[~total_mask].copy()
    sr["country"] = sr["country"].astype(str).str.strip()
    metric_cols = ["confirmed cases", "suspected cases", "confirmed deaths", "suspected deaths"]
    for c in metric_cols:
        sr[c] = pd.to_numeric(sr[c], errors="coerce").fillna(0).astype(int)
    grouped = sr.groupby("country", as_index=False)[metric_cols].sum()
    grouped["total"] = grouped["confirmed cases"] + grouped["suspected cases"]
    grouped = grouped[grouped["total"] > 0].sort_values("total", ascending=False)

    def total_metric(col):
        if total_row is None:
            return None
        v = pd.to_numeric(total_row[col], errors="coerce")
        return None if pd.isna(v) else int(v)

    def credit_excess(col):
        per_zone_sum = int(grouped[col].sum())
        total_val = total_metric(col)
        if total_val is None or grouped.empty:
            return per_zone_sum if total_val is None else total_val
        if total_val <= per_zone_sum:
            return per_zone_sum
        primary = grouped["total"].idxmax()
        grouped.loc[primary, col] += (total_val - per_zone_sum)
        return total_val

    final_conf   = credit_excess("confirmed cases")
    final_susp   = credit_excess("suspected cases")
    final_conf_d = credit_excess("confirmed deaths")
    final_susp_d = credit_excess("suspected deaths")

    per_country = []
    for _, r in grouped.iterrows():
        per_country.append({
            "country": str(r["country"]),
            "confirmed_cases": int(r["confirmed cases"]),
            "suspected_cases": int(r["suspected cases"]),
            "confirmed_deaths": int(r["confirmed deaths"]),
            "suspected_deaths": int(r["suspected deaths"]),
            "total": int(r["confirmed cases"]) + int(r["suspected cases"]),
        })
    out.update({
        "global_confirmed_cases":  final_conf,
        "global_suspected_cases":  final_susp,
        "global_confirmed_deaths": final_conf_d,
        "global_suspected_deaths": final_susp_d,
        "global_total_cases":      final_conf + final_susp,
        "affected_countries":      [c["country"] for c in per_country],
        "affected_country_count":  len(per_country),
        "per_country":             per_country,
    })
    return out


def build_active_case_markers(zone_data: dict[str, dict],
                              centroids: dict[str, tuple[float, float]]
                              ) -> list[dict]:
    """One marker per zone with one or more observed cases, placed at the
    real centroid for that zone."""
    out: list[dict] = []
    for nom, rec in zone_data.items():
        if nom not in centroids:
            continue
        susp = int(rec.get("suspected_cases") or 0)
        conf = int(rec.get("confirmed_cases") or 0)
        total = susp + conf
        if total <= 0:
            continue
        lon, lat = centroids[nom]
        out.append({
            "nom": nom,
            "name": rec.get("name", nom),
            "lat": lat,
            "lon": lon,
            "confirmed": conf,
            "suspected": susp,
            "confirmed_deaths": int(rec.get("confirmed_deaths") or 0),
            "suspected_deaths": int(rec.get("suspected_deaths") or 0),
            "total": total,
        })
    return out


# ---------------------------------------------------------------------------
# methods + terms text
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_BULLET_PREFIXES = ("•", "•", "−", "—", "-")


def _strip_bullet(s: str) -> str:
    for prefix in _BULLET_PREFIXES:
        if s.startswith(prefix):
            return s[len(prefix):].lstrip()
    return s.lstrip()


def load_methods_html() -> str:
    """Render Contributors_Methods_Data_website.docx as an HTML snippet.

    Headings 1/2/3 -> h2/h3/h4. Bold-only paragraphs are promoted to h2 as a
    fallback for documents that mark sections with bold runs only. Bullet
    glyphs (•, −, —) at the start of a paragraph are folded into a <ul>.
    Hyperlinks are preserved with target=_blank. Email addresses become
    mailto: links.
    """
    if not METHODS_DOCX.exists():
        return ""
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph
    except Exception:
        return ("<p style='color:#c66'>python-docx not installed; cannot render "
                f"{METHODS_DOCX.name}.</p>")
    d = Document(METHODS_DOCX)
    rid_to_url: dict[str, str] = {}
    for rid, rel in d.part.rels.items():
        if "hyperlink" in rel.reltype.lower():
            rid_to_url[rid] = getattr(rel, "target_ref", None) or rel._target

    def _linkify(html: str) -> str:
        return _EMAIL_RE.sub(
            lambda m: f'<a href="mailto:{m.group(1)}">{m.group(1)}</a>',
            html,
        )

    def _runs_to_html(node) -> str:
        out: list[str] = []
        for child in node.iterchildren():
            tag = child.tag
            if tag == qn("w:r"):
                txt = "".join(t.text or "" for t in child.iter(qn("w:t")))
                if txt:
                    out.append(_linkify(_html_escape(txt)))
            elif tag == qn("w:hyperlink"):
                rid = child.get(qn("r:id")) or child.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                txt = "".join(t.text or "" for t in child.iter(qn("w:t")))
                url = rid_to_url.get(rid, "")
                if txt and url:
                    out.append(
                        f'<a href="{_html_escape(url)}" target="_blank" rel="noopener">'
                        f"{_html_escape(txt)}</a>"
                    )
                elif txt:
                    out.append(_linkify(_html_escape(txt)))
        return "".join(out)

    def _table_html(tbl_el) -> str:
        rows_html: list[str] = []
        for ri, tr in enumerate(tbl_el.iterfind(qn("w:tr"))):
            cells_html: list[str] = []
            for tc in tr.iterfind(qn("w:tc")):
                pieces = []
                for p in tc.iterfind(qn("w:p")):
                    s = _runs_to_html(p)
                    if s:
                        pieces.append(s)
                cells_html.append("<br/>".join(pieces))
            tag = "th" if ri == 0 else "td"
            rows_html.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells_html) + "</tr>")
        return "<table class='methods-table'>" + "".join(rows_html) + "</table>"

    def _is_bold_heading(p) -> bool:
        runs = [r for r in p.runs if (r.text or "").strip()]
        return bool(runs) and all(bool(r.bold) for r in runs)

    parts: list[str] = []
    in_ul = False
    for child in d.element.body.iterchildren():
        tag = child.tag
        if tag == qn("w:tbl"):
            if in_ul:
                parts.append("</ul>")
                in_ul = False
            parts.append(_table_html(child))
            continue
        if tag != qn("w:p"):
            continue
        para = Paragraph(child, d.part)
        txt = (para.text or "").strip()
        style = para.style.name if para.style else "Normal"
        if not txt:
            if in_ul:
                parts.append("</ul>")
                in_ul = False
            continue
        html_body = _runs_to_html(child)
        is_bullet = any(txt.startswith(p) for p in _BULLET_PREFIXES[:-1])
        if is_bullet:
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            parts.append(f"<li>{_strip_bullet(html_body)}</li>")
            continue
        if in_ul:
            parts.append("</ul>")
            in_ul = False
        if style.startswith("Title") or style.startswith("Heading 1"):
            parts.append(f"<h2>{html_body}</h2>")
        elif style.startswith("Heading 2"):
            parts.append(f"<h3>{html_body}</h3>")
        elif style.startswith("Heading 3"):
            parts.append(f"<h4>{html_body}</h4>")
        elif _is_bold_heading(para):
            parts.append(f"<h2>{html_body}</h2>")
        else:
            parts.append(f"<p>{html_body}</p>")
    if in_ul:
        parts.append("</ul>")
    return "\n".join(parts)


_TERMS_SECTION_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_TERMS_LASTUPDATED_RE = re.compile(r"^Last updated:\s*(.+)$", re.IGNORECASE)


def load_terms_html() -> tuple[str, str]:
    if not TERMS_TXT.exists():
        return "", ""
    last_updated = ""
    parts: list[str] = []
    for line in TERMS_TXT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.lower() == "terms of use":
            continue
        m = _TERMS_LASTUPDATED_RE.match(line)
        if m:
            last_updated = m.group(1).strip()
            continue
        m = _TERMS_SECTION_RE.match(line)
        if m:
            parts.append(f"<h3>{_html_escape(m.group(1))}. {_html_escape(m.group(2))}</h3>")
            continue
        text = _html_escape(line)
        text = _EMAIL_RE.sub(
            lambda mm: f"<a href='mailto:{mm.group(1)}'>{mm.group(1)}</a>",
            text,
        )
        parts.append(f"<p>{text}</p>")
    return "\n".join(parts), last_updated


# ---------------------------------------------------------------------------
# partner logos
# ---------------------------------------------------------------------------

_LOGO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def load_logo_data_uri(filename: str) -> str:
    path = BRANDING_DIR / filename
    if not path.exists():
        return ""
    mime = _LOGO_MIME.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def load_partners() -> list[dict]:
    if not BRANDING_DIR.exists():
        return []
    url_map: dict[str, str] = {}
    if BRANDING_URLS.exists():
        for line in BRANDING_URLS.read_text(encoding="utf-8").splitlines():
            if "," not in line:
                continue
            fname, url = line.split(",", 1)
            url_map[fname.strip()] = url.strip()
    out: list[dict] = []
    for fname in PARTNER_ORDER:
        uri = load_logo_data_uri(fname)
        if uri:
            out.append({
                "alt": Path(fname).stem.upper(),
                "href": url_map.get(fname, ""),
                "data_uri": uri,
            })
    return out


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------

LAYER_DEFS = [
    # (group, layer_id, label, csv_col, palette, scale)
    ("Observed (epi update)",  "obs::total",     "Total cases (confirmed + suspected)",              "total_cases",      "reds",     "log"),
    ("Observed (epi update)",  "obs::confirmed", "Confirmed cases",                                 "confirmed_cases",  "reds",     "log"),
    ("Observed (epi update)",  "obs::suspected", "Suspected cases",                                 "suspected_cases",  "reds",     "log"),
    ("Observed (epi update)",  "obs::conf_d",    "Confirmed deaths",                                "confirmed_deaths", "reds",     "log"),
    ("Observed (epi update)",  "obs::susp_d",    "Suspected deaths",                                "suspected_deaths", "reds",     "log"),
    ("Modeled projection",     "cal::true",      "Relative risk",                                   "relative_risk",    "viridis",  "log"),
    ("Population",             "pop::point",     "Population (point estimate)",                     "population",       "viridis",  "log"),
    ("Population",             "pop::lower",     "Population (lower bound)",                        "population_lower", "viridis",  "log"),
    ("Population",             "pop::upper",     "Population (upper bound)",                        "population_upper", "viridis",  "log"),
    ("Health system",          "hf::count",      "Health facilities (count)",                       "n_health_facilities",          "viridis", "log"),
    ("Refugee/IDP camps",      "ref::nearest",   "Distance to nearest camp (km)",                   "nearest_refugee_camp_km",      "plasma_r", "log"),
    ("Refugee/IDP camps",      "ref::n50",       "# camps within 50 km",                            "n_refugee_camps_within_50km",  "reds",     "linear"),
    ("Refugee/IDP camps",      "ref::n100",      "# camps within 100 km",                           "n_refugee_camps_within_100km", "reds",     "linear"),
    ("Incoming Mobility",      "disp::in",       "Incoming displaced persons (12mo)",               "displaced_in_individuals_12mo", "reds",     "log"),
    ("Incoming Mobility",      "flow::in",       "Flowminder incoming travel",                      "flowminder_in_mar2026",         "reds",     "log"),
    ("Distance from Mongbalu","d::travel",      "Travel time from Mongbalu (hours)",              "travel_time_to_mongbwalu_h",                    "plasma_r", "linear"),
    ("Distance from Mongbalu","d::geo",         "Road distance from Mongbalu (km)",           "geodesic_to_mongbwalu_km",                      "plasma_r", "linear"),
    ("Distance from Mongbalu","d::eff",         "Effective distance from Mongbalu",               "calibration_effective_distance_from_mongbwalu", "plasma_r", "linear"),
    ("Health system",          "hf::pcr",        "PCR Testing Capacity",                            "n_pcr_tests_target",                             "viridis", "log"),
]

# Relative risk layer is masked below this threshold (rendered as "no data").
PROJECTION_MASK_LAYERS = {"cal::true"}
PROJECTION_MASK_FIELD = "relative_risk"
PROJECTION_MASK_MIN = 0.005

LAYER_SOURCE_TEXT: dict[str, str] = {
    "Observed (epi update)":     "",
    "Modeled projection":        "",
    "Population":                "Source: GRID3 v4.4 gridded population, zonal sums",
    "Health system":             "Source: GRID3 health facilities v8",
    "Refugee/IDP camps":         "Source: OpenStreetMap (amenity=refugee_site), per-zone distance/count",
    "Incoming Mobility":         "Sources: aggregated displacement movement matrices; Flowminder Mar 2026 inflows",
    "Distance from Mongbalu":   "Source: OSRM driving table (travel time), great-circle distance, calibrated kernel",
}


# ---------------------------------------------------------------------------
# payload assembly
# ---------------------------------------------------------------------------

def build_payload() -> dict:
    print(f"BUILD_DIR  = {BUILD_DIR}")
    print(f"DATA_ROOT  = {DATA_ROOT}")

    features, centroids_by_nom = load_features_from_geojson()
    print(f"  loaded {len(features)} zone polygons from {BUILD_GEOJSON.name}")

    zone_data, case_totals = load_metadata(centroids_by_nom)
    print(f"  assembled metadata for {len(zone_data)} zones")

    initial_view = None
    if "Bunia" in centroids_by_nom:
        lon, lat = centroids_by_nom["Bunia"]
        initial_view = {"lat": lat, "lon": lon, "zoom": 8}

    layers = [
        {"group": group, "id": lid, "label": label, "field": field,
         "palette": palette, "scale": scale,
         "source": LAYER_SOURCE_TEXT.get(group, "")}
        for (group, lid, label, field, palette, scale) in LAYER_DEFS
    ]

    methods_html = load_methods_html()
    print(f"  methods HTML: {len(methods_html)} chars")
    terms_html, terms_updated = load_terms_html()
    print(f"  terms HTML: {len(terms_html)} chars (updated {terms_updated!r})")
    partners = load_partners()
    print(f"  partner logos: {[p['alt'] for p in partners]}")
    sitrep = compute_global_sitrep_totals()
    if sitrep is None:
        sitrep = {
            "global_confirmed_cases":  case_totals.get("confirmed_cases", 0),
            "global_suspected_cases":  case_totals.get("suspected_cases", 0),
            "global_confirmed_deaths": case_totals.get("confirmed_deaths", 0),
            "global_suspected_deaths": case_totals.get("suspected_deaths", 0),
            "global_total_cases":      case_totals.get("confirmed_cases", 0)
                                       + case_totals.get("suspected_cases", 0),
            "affected_countries": ["DRC"],
            "affected_country_count": 1,
            "per_country": [{
                "country": "DRC",
                "confirmed_cases":  case_totals.get("confirmed_cases", 0),
                "suspected_cases":  case_totals.get("suspected_cases", 0),
                "confirmed_deaths": case_totals.get("confirmed_deaths", 0),
                "suspected_deaths": case_totals.get("suspected_deaths", 0),
                "total": case_totals.get("confirmed_cases", 0)
                         + case_totals.get("suspected_cases", 0),
            }],
        }
    totals = {**case_totals, **sitrep}
    print(f"  case totals: confirmed={totals.get('confirmed_cases', 0)}, "
          f"suspected={totals.get('suspected_cases', 0)}, "
          f"affected zones={totals.get('affected_zones', 0)}")
    active_case_markers = build_active_case_markers(zone_data, centroids_by_nom)
    print(f"  active-case markers: {len(active_case_markers)} zones")

    asof = detect_asof()
    print(f"  asof: {asof}")

    return {
        "asof": asof,
        "travel_from": TRAVEL_FROM_ZONE,
        "initial_view": initial_view,
        "insp_sitrep_url": latest_insp_url(),
        "geometry": {"type": "FeatureCollection", "features": features},
        "zone_data": zone_data,
        "layers": layers,
        "projection_mask": {
            "layers": sorted(PROJECTION_MASK_LAYERS),
            "field": PROJECTION_MASK_FIELD,
            "min": PROJECTION_MASK_MIN,
        },
        "methods_html": methods_html,
        "terms_html": terms_html,
        "terms_updated": terms_updated,
        "partners": partners,
        "totals": totals,
        "active_case_markers": active_case_markers,
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>DRC Ebola Bundibugyo 2026 — interactive dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html, body { margin:0; padding:0; height:100%; font-family: -apple-system, system-ui, "Segoe UI", Helvetica, Arial, sans-serif; background:#111; color:#eee; }
  #map { position:absolute; top:0; right:0; bottom:0; left:0; }
  .panel {
    position:absolute; z-index:1000;
    background:rgba(20,20,20,0.92); color:#f4f4f4;
    padding:12px 14px; border-radius:8px;
    box-shadow:0 2px 10px rgba(0,0,0,0.4);
    font-size:13px; line-height:1.4;
  }
  #controls     { top:12px; left:12px; max-width:340px; }
  #legend       { bottom:24px; left:12px; max-width:300px; }
  #info         { top:12px; right:12px; max-width:340px; max-height:80vh; overflow-y:auto; }
  #info-header,
  .panel-header { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  #info-toggle,
  .panel-toggle { background:transparent; color:#ffd28a; border:1px solid #555; border-radius:4px;
                  width:22px; height:22px; padding:0; cursor:pointer; font-size:14px; line-height:1; }
  #info-toggle:hover,
  .panel-toggle:hover { background:#333; color:#ffae42; }
  #info.collapsed #info-body,
  .panel.collapsed .panel-body { display:none; }
  @media (max-width: 700px) {
    .panel          { font-size:12px; padding:6px 8px; }
    #title          { min-width:unset; max-width:calc(100vw - 24px); padding:6px 8px; }
    #title h1       { margin-bottom:2px; }
    #title .sub     { font-size:10px; }
    #tracker        { margin-top:4px; padding:4px 2px 0; }
    #tracker .global-row { gap:14px; }
    #tracker .countries-row { gap:2px; margin-top:6px; }
    #tracker .country { gap:3px 6px; }
    #info           { max-width:60vw; }
    #legend         { max-width:60vw; }
    #controls       { top:clamp(150px, 28vh, 240px); }
    #info           { top:clamp(150px, 28vh, 240px); }
  }
  @media (max-height: 500px) {
    .panel          { font-size:11px; padding:5px 7px; }
    #title          { padding:4px 8px; }
    #title h1       { font-size:clamp(14px, 4vh, 18px); margin-bottom:1px; letter-spacing:0.2px; }
    #title .sub     { font-size:9px; }
    #title .link-btn { padding:1px 6px; font-size:10px; }
    #tracker        { margin-top:2px; padding:3px 2px 0; }
    #tracker .global-title { font-size:9px; margin-bottom:0; }
    #tracker .global-row { gap:clamp(8px, 3vh, 16px); }
    #tracker .global-cell .num { font-size:clamp(16px, 4.5vh, 22px); }
    #tracker .global-cell .sub { font-size:9px; margin-top:0; }
    #tracker .countries-row { margin-top:3px; font-size:10px; gap:1px; }
    #info           { max-height:70vh; }
    #legend         { max-height:60vh; bottom:8px; }
    #partners      { bottom:8px; right:6px; padding:2px 3px; gap:2px;
                      width:auto; max-width:min(38vw, 260px); }
    #partners a    { flex:0 0 calc(50% - 1px); }
    #partners img  { max-width:100%; max-height:clamp(18px, 5vh, 28px);
                      height:auto; width:auto; object-fit:contain; }
  }
  #title        { top:12px; left:50%; transform:translateX(-50%); text-align:center; min-width:min(520px, calc(100vw - 24px)); max-width:calc(100vw - 24px); box-sizing:border-box; }
  #title .title-row { display:flex; align-items:center; justify-content:center; gap:14px; }
  /* Tracker stack (all rows centered relative to the panel). */
  #tracker { display:flex; flex-direction:column; align-items:center; margin-top:6px; padding:6px 4px 0; border-top:1px solid #333; }
  #tracker .stats-block  { display:flex; flex-direction:column; align-items:center; }
  #tracker .global-title { font-size:clamp(9px, 1.5vw, 10px); color:#bbb; text-transform:uppercase; letter-spacing:0.6px; margin-bottom:2px; text-align:center; }
  #tracker .global-row { display:flex; align-items:flex-end; gap:clamp(14px, 6vw, 36px); line-height:1.05; }
  #tracker .global-cell { display:flex; flex-direction:column; align-items:center; }
  #tracker .global-cell .num { font-size:clamp(20px, 6vw, 30px); font-weight:700; font-variant-numeric: tabular-nums; line-height:1; }
  #tracker .global-cell .sub { font-size:clamp(9px, 1.5vw, 10px); color:#bbb; text-transform:uppercase; letter-spacing:0.6px; margin-top:2px; }
  #tracker .global-cell.cases  .num { color:#ffd166; }
  #tracker .global-cell.deaths .num { color:#ff4d4d; }
  #tracker .countries-row { display:flex; flex-direction:column; align-items:center; gap:3px; margin-top:8px; font-size:clamp(10px, 1.6vw, 11px); color:#ddd; }
  #tracker .country { display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 8px; justify-content:center; }
  #tracker .country .name { color:#9fcdfb; font-weight:600; }
  #tracker .country .nums { font-variant-numeric: tabular-nums; }
  #tracker .country .conf   { color:#ff6b6b; font-weight:600; }
  #tracker .country .susp   { color:#ffae42; font-weight:600; }
  #tracker .country .conf-d { color:#c97a8a; font-weight:600; }
  #tracker .country .susp-d { color:#caa385; font-weight:600; }
  #tracker .country .dot { color:#444; }
  #tracker .country .sub { font-size:10px; color:#888; }
  #title h1 { margin:0 0 4px 0; font-size:clamp(16px, 3.4vw, 22px); font-weight:700; letter-spacing:0.3px; }
  #title .sub { font-size:11px; opacity:0.8; }
  select, button { background:#222; color:#eee; border:1px solid #444; padding:4px 6px; border-radius:4px; font-size:12px; }
  label { display:block; margin-top:6px; font-size:12px; color:#bbb; }
  .swatch { display:inline-block; width:18px; height:12px; margin-right:6px; vertical-align:middle; border:1px solid #444; }
  .legend-bar { display:block; width:240px; height:12px; }
  .legend-ticks { display:flex; justify-content:space-between; font-size:10px; color:#aaa; width:240px; margin-top:2px; }
  .legend-ticks span { display:inline-block; white-space:nowrap; }
  .legend-ticks span:nth-child(1) { text-align:left;   flex:1; }
  .legend-ticks span:nth-child(2) { text-align:center; flex:1; }
  .legend-ticks span:nth-child(3) { text-align:right;  flex:1; }
  .legend-scale { font-size:10px; color:#888; margin-top:2px; }
  table { border-collapse:collapse; font-size:12px; width:100%; }
  table td { padding:2px 6px; vertical-align:top; }
  table td:first-child { color:#aaa; white-space:nowrap; }
  .info-empty { color:#888; font-style:italic; }
  .footer { font-size:10px; color:#888; margin-top:8px; }
  .checkbox-row { display:flex; align-items:center; margin-top:6px; gap:6px; }
  .case-icon { width:14px; height:14px; border-radius:50%; background:#ff1f4d; border:2px solid #fff; box-shadow:0 0 6px rgba(255,31,77,0.95); }
  h4 { margin: 8px 0 2px 0; font-size: 12px; color: #ffd28a; font-weight: 600; }
  .link-btn {
    display:inline-block; margin-top:4px; padding:2px 8px;
    background:#222; color:#ffd28a; text-decoration:none;
    border:1px solid #555; border-radius:4px; font-size:11px;
    cursor:pointer;
  }
  .link-btn:hover { background:#333; color:#ffae42; border-color:#ffae42; }
  .modal {
    display:none; position:fixed; z-index:5000;
    top:0; left:0; right:0; bottom:0;
    background:rgba(0,0,0,0.6);
    align-items:flex-start; justify-content:center;
    padding:40px 20px; overflow-y:auto;
  }
  .modal.open { display:flex; }
  .modal .sheet {
    background:#1a1a1a; color:#eee;
    max-width:780px; width:100%;
    padding:28px 32px; border-radius:8px;
    box-shadow:0 6px 24px rgba(0,0,0,0.6);
    line-height:1.55; font-size:14px;
  }
  .modal .close {
    float:right; cursor:pointer; font-size:18px; color:#aaa;
    background:none; border:none; padding:0;
  }
  .modal .close:hover { color:#ffae42; }
  .modal h2 { margin:0 0 4px 0; font-size:18px; color:#ffd28a; }
  .modal h3 { margin:18px 0 4px 0; font-size:15px; color:#ffd28a; }
  .modal h4 { margin:12px 0 4px 0; font-size:13px; color:#ffd28a; }
  .modal p, .modal li { margin:6px 0; }
  .modal ul { margin:6px 0 6px 20px; }
  .modal a { color:#9fcdfb; text-decoration:underline; }
  .modal a:hover { color:#ffae42; }
  .modal .methods-table { border-collapse:collapse; margin:10px 0; font-size:12px; width:100%; }
  .modal .methods-table th, .modal .methods-table td {
    border:1px solid #3a3a3a; padding:4px 8px; text-align:left; vertical-align:top;
  }
  .modal .methods-table th { background:#262626; color:#ffd28a; font-weight:600; }
  .modal .methods-table tr:nth-child(even) td { background:#1f1f1f; }
  #partners { position:absolute; bottom:12px; right:8px; z-index:1000;
              background:#ffffff; border-radius:4px; padding:2px 3px;
              box-shadow:0 2px 8px rgba(0,0,0,0.4);
              display:flex; flex-wrap:wrap; align-items:center;
              justify-content:center; gap:2px;
              max-width:min(80vw, 720px); }
  #partners a { display:inline-flex; align-items:center; transition:opacity .15s ease; }
  #partners a:hover { opacity:0.78; }
  #partners img { height:clamp(24px, 5vmin, 44px); width:auto;
                  max-width:min(22vmin, 140px); display:block; object-fit:contain; }
</style>
</head>
<body>
<div id="map"></div>
<div id="partners"></div>
<div id="title" class="panel">
  <h1>DRC Ebola Bundibugyo 2026</h1>
  <div class="sub" id="title-sub"></div>
  <div class="sub" id="title-disclaimer">Case Data From INSP - All Underlying Data Have Been Released Publicly</div>
  <div id="tracker"></div>
  <div style="margin-top:4px">
    <button id="methods-btn" class="link-btn" type="button">Contributors, Data, and Methods</button>
    <button id="terms-btn"   class="link-btn" type="button">Terms of Use</button>
  </div>
</div>
<div id="methods-modal" class="modal" role="dialog" aria-label="Contributors, Data, and Methods" aria-modal="true">
  <div class="sheet">
    <button class="close" id="methods-close" aria-label="Close">✕</button>
    <h2>Contributors, Data, and Methods</h2>
    <div id="methods-content"></div>
  </div>
</div>
<div id="terms-modal" class="modal" role="dialog" aria-label="Terms of Use" aria-modal="true">
  <div class="sheet">
    <button class="close" id="terms-close" aria-label="Close">✕</button>
    <h2>Terms of Use</h2>
    <div id="terms-updated" style="font-size:11px;color:#888;margin-bottom:10px"></div>
    <div id="terms-content"></div>
  </div>
</div>
<div id="controls" class="panel">
  <div class="panel-header">
    <strong>Layer</strong>
    <button class="panel-toggle" data-target="controls" type="button" aria-label="Toggle layer controls" title="Collapse / expand layer controls">−</button>
  </div>
  <div class="panel-body">
    <label for="layer-select">Source</label>
    <select id="layer-select"></select>
    <label for="scale-select">Color scale</label>
    <select id="scale-select">
      <option value="log">log</option>
      <option value="linear">linear</option>
    </select>
    <div class="checkbox-row">
      <input type="checkbox" id="show-cases" />
      <label for="show-cases" style="margin:0;color:#eee">Show active-case markers</label>
    </div>
    <div class="footer" id="layer-meta"></div>
  </div>
</div>
<div id="legend" class="panel">
  <div class="panel-header">
    <div id="legend-title"><strong>Legend</strong></div>
    <button class="panel-toggle" data-target="legend" type="button" aria-label="Toggle legend" title="Collapse / expand legend">−</button>
  </div>
  <div class="panel-body">
    <div class="legend-bar" id="legend-bar"></div>
    <div class="legend-ticks" id="legend-ticks"></div>
    <div class="legend-scale" id="legend-scale"></div>
    <div id="legend-gray" style="font-size:11px;color:#bbb;margin-top:4px"></div>
  </div>
</div>
<div id="info" class="panel">
  <div id="info-header">
    <strong>Zone</strong>
    <button id="info-toggle" type="button" aria-label="Toggle zone details" title="Collapse / expand zone details">−</button>
  </div>
  <div id="info-body" class="info-empty">Hover a health zone.</div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const PAYLOAD = JSON.parse(document.getElementById("payload").textContent);
const ZONE_DATA = PAYLOAD.zone_data;
const LAYERS = PAYLOAD.layers;
const TRAVEL_FROM = PAYLOAD.travel_from || "Mongbwalu";

document.getElementById("title-sub").innerHTML =
  "Latest " +
  "<a href='" + (PAYLOAD.insp_sitrep_url || "https://insp.cd/") + "' target='_blank' rel='noopener' " +
  "style='color:#9fcdfb;text-decoration:underline'>INSP Sit Rep</a>" +
  " - " + PAYLOAD.asof;

// --- case-count tracker ---
(function buildTracker() {
  const t = PAYLOAD.totals || {};
  const tracker = document.getElementById("tracker");
  function num(v) { return (v == null ? 0 : v).toLocaleString(); }
  const per = (t.per_country || []);
  const countryHTML = per.map(function(c) {
    return "<div class='country'>" +
             "<span class='name'>" + c.country + "</span>" +
             "<span class='nums'>" +
               "<span class='conf'>"   + num(c.confirmed_cases)   + "</span> conf · " +
               "<span class='susp'>"   + num(c.suspected_cases)   + "</span> susp · " +
               "<span class='conf-d'>" + num(c.confirmed_deaths)  + "</span> conf deaths · " +
               "<span class='susp-d'>" + num(c.suspected_deaths)  + "</span> susp deaths" +
             "</span>" +
           "</div>";
  }).join("");
  const globalDeaths = (t.global_confirmed_deaths || 0) + (t.global_suspected_deaths || 0);
  tracker.innerHTML =
    "<div class='stats-block'>" +
      "<div class='global-title'>outbreak size (confirmed + suspected)</div>" +
      "<div class='global-row'>" +
        "<div class='global-cell cases'>" +
          "<div class='num'>" + num(t.global_total_cases) + "</div>" +
          "<div class='sub'>cases</div>" +
        "</div>" +
        "<div class='global-cell deaths'>" +
          "<div class='num'>" + num(globalDeaths) + "</div>" +
          "<div class='sub'>deaths</div>" +
        "</div>" +
      "</div>" +
    "</div>" +
    "<div class='countries-row'>" + (countryHTML || "<span class='sub'>—</span>") + "</div>";
})();

// --- partners strip ---
(function buildPartners() {
  const partners = PAYLOAD.partners || [];
  const root = document.getElementById("partners");
  if (!partners.length || !root) { if (root) root.style.display="none"; return; }
  root.innerHTML = partners.map(function(p) {
    const img = "<img src='" + p.data_uri + "' alt='" + p.alt + "' title='" + p.alt + "' />";
    return p.href
      ? "<a href='" + p.href + "' target='_blank' rel='noopener'>" + img + "</a>"
      : img;
  }).join("");
})();

const layerSelect = document.getElementById("layer-select");
const scaleSelect = document.getElementById("scale-select");
const layerMeta = document.getElementById("layer-meta");

(function buildSelect() {
  const groups = {};
  for (const L of LAYERS) {
    if (!groups[L.group]) {
      const og = document.createElement("optgroup");
      og.label = L.group;
      layerSelect.appendChild(og);
      groups[L.group] = og;
    }
    const o = document.createElement("option");
    o.value = L.id; o.textContent = L.label;
    groups[L.group].appendChild(o);
  }
})();

function getLayer(id) { return LAYERS.find(L => L.id === id); }

// color palettes
const PLASMA = [
  [13,8,135],[75,3,161],[125,3,168],[168,34,150],[203,70,121],
  [229,107,93],[248,148,65],[253,195,40],[240,249,33]];
const REDS = [
  [255,245,235],[254,217,181],[253,173,118],[252,127,73],[239,77,55],
  [205,32,32],[140,17,17]];
const VIRIDIS = [
  [68,1,84],[72,40,120],[62,73,137],[49,104,142],[38,130,142],[31,158,137],
  [53,183,121],[109,206,89],[180,222,44],[253,231,37]];
const PALETTES = {plasma:PLASMA, plasma_r:[...PLASMA].reverse(), reds:REDS, viridis:VIRIDIS};

function lerpColor(stops, t) {
  if (t <= 0) return stops[0];
  if (t >= 1) return stops[stops.length - 1];
  const s = t * (stops.length - 1);
  const i = Math.floor(s), f = s - i;
  const a = stops[i], b = stops[i + 1];
  return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f];
}
function rgb(c) { return "rgb(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," + Math.round(c[2]) + ")"; }

const PROJ_MASK = PAYLOAD.projection_mask || null;
const PROJ_MASK_LAYERS = new Set((PROJ_MASK && PROJ_MASK.layers) || []);

function valueForZone(zone, layer) {
  if (PROJ_MASK && PROJ_MASK_LAYERS.has(layer.id)) {
    const m = zone[PROJ_MASK.field];
    if (m == null || Number.isNaN(m) || Number(m) < PROJ_MASK.min) return null;
  }
  const v = zone[layer.field];
  return (v == null || Number.isNaN(v)) ? null : Number(v);
}

// --- map setup ---
const INITIAL_VIEW = PAYLOAD.initial_view || {lat: -2.5, lon: 22.5, zoom: 5};
const map = L.map("map").setView([INITIAL_VIEW.lat, INITIAL_VIEW.lon], INITIAL_VIEW.zoom);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>, &copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains: "abcd", maxZoom: 19
}).addTo(map);

const NO_DATA_FILL = "#3a3a3a";
const ZERO_FILL    = "#5a5a5a";
let currentValues = new Map();
let currentDomain = {min:0, max:1, isLog:true, palette:REDS};

function recompute() {
  const layer = getLayer(layerSelect.value);
  currentValues.clear();
  const positives = [];
  let lo = Infinity, hi = -Infinity;
  for (const feat of PAYLOAD.geometry.features) {
    const ref = feat.properties.nom;
    const zone = ZONE_DATA[ref];
    if (!zone) continue;
    const v = valueForZone(zone, layer);
    if (v == null || Number.isNaN(v)) continue;
    currentValues.set(ref, v);
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    if (v > 0) positives.push(v);
  }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  const useLog = scaleSelect.value === "log" && positives.length > 0;
  let dlo, dhi;
  if (useLog) {
    dlo = Math.min.apply(null, positives);
    dhi = Math.max.apply(null, positives);
    if (dhi === dlo) dhi = dlo * 10;
  } else {
    dlo = Math.min(0, lo);
    dhi = (hi === dlo) ? dlo + 1 : hi;
  }
  currentDomain = {min:dlo, max:dhi, isLog:useLog, palette:PALETTES[layer.palette] || PLASMA};
  geoLayer.setStyle(styleFn);
  updateLegend(layer);
  updateLayerMeta(layer);
}

function valueToColor(v) {
  if (v == null || Number.isNaN(v)) return NO_DATA_FILL;
  const d = currentDomain;
  if (d.isLog && v <= 0) return ZERO_FILL;
  let t;
  if (d.isLog) t = (Math.log(v) - Math.log(d.min)) / (Math.log(d.max) - Math.log(d.min));
  else t = (v - d.min) / (d.max - d.min || 1);
  if (!isFinite(t)) t = 0;
  t = Math.max(0, Math.min(1, t));
  return rgb(lerpColor(d.palette, t));
}

function styleFn(feature) {
  const ref = feature.properties.nom;
  const v = currentValues.get(ref);
  const has = v != null && !Number.isNaN(v);
  const isZero = has && (currentDomain.isLog ? v <= 0 : v === 0);
  return {
    color:"#111", weight:0.35,
    fillColor: valueToColor(v),
    fillOpacity: (!has || isZero) ? 0.55 : 0.85
  };
}

function fmtLegend(v, kind) {
  if (v == null || Number.isNaN(v)) return "—";
  if (typeof v !== "number") return String(v);
  if (kind === "rel") return v.toFixed(2);
  if (kind === "int") return Math.round(v).toLocaleString();
  return v.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1});
}

function fmt(v, kind) {
  if (v == null || Number.isNaN(v)) return "—";
  if (typeof v !== "number") return String(v);
  if (kind === "rel") return v.toFixed(2);
  if (kind === "cal") {
    if (Math.abs(v) < 1) return v.toFixed(1);
    return Math.round(v).toLocaleString();
  }
  return Math.round(v).toLocaleString();
}

function updateLayerMeta(layer) {
  layerMeta.innerHTML = layer.source || "";
}

function updateLegend(layer) {
  document.getElementById("legend-title").innerHTML = "<strong>" + layer.label + "</strong>";
  const stops = [];
  const N = 32;
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    stops.push(rgb(lerpColor(currentDomain.palette, t)) + " " + Math.round(t * 100) + "%");
  }
  document.getElementById("legend-bar").style.background = "linear-gradient(to right, " + stops.join(", ") + ")";
  const ticks = document.getElementById("legend-ticks");
  const lo = currentDomain.min, hi = currentDomain.max;
  const mid = currentDomain.isLog ? Math.sqrt(lo * hi) : (lo + hi) / 2;
  const fmtKind = layer.id === "cal::true" ? "rel" : "int";
  ticks.innerHTML =
    "<span>" + fmtLegend(lo,  fmtKind) + "</span>" +
    "<span>" + fmtLegend(mid, fmtKind) + "</span>" +
    "<span>" + fmtLegend(hi,  fmtKind) + "</span>";
  document.getElementById("legend-scale").textContent =
    currentDomain.isLog ? "(log scale)" : "(linear scale)";
  document.getElementById("legend-gray").innerHTML =
    "<span class='swatch' style='background:" + ZERO_FILL + "'></span>zero · " +
    "<span class='swatch' style='background:" + NO_DATA_FILL + "'></span>no data";
}

function infoHTML(feature) {
  const ref = feature.properties.nom;
  const z = ZONE_DATA[ref] || {};
  const name = feature.properties.name || "(unnamed)";
  let h = "<div><strong>" + name + "</strong></div>";
  h += "<div style='color:#aaa;font-size:11px;margin-bottom:6px'>" + (ref || "—") + "</div>";

  h += "<h4>Observed cases (" + PAYLOAD.asof + ")</h4>";
  h += "<table>";
  h += "<tr><td>total</td><td>" + fmt(z.total_cases) + "</td></tr>";
  h += "<tr><td>confirmed</td><td>" + fmt(z.confirmed_cases) + "</td></tr>";
  h += "<tr><td>confirmed deaths</td><td>" + fmt(z.confirmed_deaths) + "</td></tr>";
  h += "<tr><td>suspected</td><td>" + fmt(z.suspected_cases) + "</td></tr>";
  h += "<tr><td>suspected deaths</td><td>" + fmt(z.suspected_deaths) + "</td></tr>";
  h += "</table>";

  h += "<h4>Modeled projection</h4>";
  h += "<table>";
  h += "<tr><td>relative risk</td><td>" + fmt(z.relative_risk, "rel") + "</td></tr>";
  h += "</table>";

  h += "<h4>Population</h4>";
  h += "<table>";
  h += "<tr><td>point est.</td><td>" + fmt(z.population) + "</td></tr>";
  h += "<tr><td>lower</td><td>" + fmt(z.population_lower) + "</td></tr>";
  h += "<tr><td>upper</td><td>" + fmt(z.population_upper) + "</td></tr>";
  h += "</table>";

  h += "<h4>Health system</h4>";
  h += "<table>";
  h += "<tr><td>facilities</td><td>" + fmt(z.n_health_facilities) + "</td></tr>";
  h += "<tr><td>PCR testing capacity</td><td>" + fmt(z.n_pcr_tests_target) + "</td></tr>";
  h += "</table>";

  h += "<h4>Refugee/IDP camps</h4>";
  h += "<table>";
  h += "<tr><td>nearest (km)</td><td>" + fmt(z.nearest_refugee_camp_km) + "</td></tr>";
  h += "<tr><td>within 50 km</td><td>" + fmt(z.n_refugee_camps_within_50km) + "</td></tr>";
  h += "<tr><td>within 100 km</td><td>" + fmt(z.n_refugee_camps_within_100km) + "</td></tr>";
  h += "</table>";

  h += "<h4>Incoming Mobility</h4>";
  h += "<table>";
  h += "<tr><td>displaced persons (12mo)</td><td>" + fmt(z.displaced_in_individuals_12mo) + "</td></tr>";
  h += "<tr><td>Flowminder travel (Mar 2026)</td><td>" + fmt(z.flowminder_in_mar2026) + "</td></tr>";
  h += "</table>";

  h += "<h4>Distance from " + TRAVEL_FROM + "</h4>";
  h += "<table>";
  h += "<tr><td>travel time (h)</td><td>" + fmt(z.travel_time_to_mongbwalu_h) + "</td></tr>";
  h += "<tr><td>geodesic (km)</td><td>" + fmt(z.geodesic_to_mongbwalu_km) + "</td></tr>";
  h += "<tr><td>effective</td><td>" + fmt(z.calibration_effective_distance_from_mongbwalu) + "</td></tr>";
  h += "</table>";
  return h;
}

const geoLayer = L.geoJSON(PAYLOAD.geometry, {
  style: styleFn,
  onEachFeature: function (feature, layer) {
    layer.on({
      mouseover: function(e) {
        e.target.setStyle({weight: 1.6, color: "#ffae42"});
        e.target.bringToFront();
        document.getElementById("info-body").className = "";
        document.getElementById("info-body").innerHTML = infoHTML(feature);
      },
      mouseout: function(e) { geoLayer.resetStyle(e.target); },
      click: function(e) { map.fitBounds(e.target.getBounds(), {padding:[40,40]}); }
    });
  }
}).addTo(map);

// --- active-case markers ---
const ACTIVE_CASES = PAYLOAD.active_case_markers || [];
const caseIcon = L.divIcon({className:"", html:"<div class='case-icon'></div>", iconSize:[14,14]});
const caseLayer = L.layerGroup();
const showCasesBox = document.getElementById("show-cases");
for (const c of ACTIVE_CASES) {
  if (!isFinite(c.lat) || !isFinite(c.lon)) continue;
  const m = L.marker([c.lat, c.lon], {icon: caseIcon});
  const totalDeaths = (c.confirmed_deaths || 0) + (c.suspected_deaths || 0);
  m.bindTooltip(
    "<strong>" + (c.name || "(unnamed)") + "</strong><br/>" +
    "confirmed: " + c.confirmed + "  ·  suspected: " + c.suspected +
    (totalDeaths > 0 ? "<br/>deaths: " + totalDeaths : ""),
    {direction:"top", offset:[0,-8]}
  );
  caseLayer.addLayer(m);
}
showCasesBox.addEventListener("change", function() {
  if (showCasesBox.checked) caseLayer.addTo(map);
  else map.removeLayer(caseLayer);
});

// Default: Suspected cases layer, active-case markers ON, centered on Bunia.
layerSelect.value = "obs::total";
showCasesBox.checked = true;
caseLayer.addTo(map);

layerSelect.addEventListener("change", recompute);
scaleSelect.addEventListener("change", recompute);
recompute();

// --- modal wiring (Methods + Terms) ---
function wireModal(modalId, btnId, closeId) {
  const modal = document.getElementById(modalId);
  const btn = document.getElementById(btnId);
  const closeBtn = document.getElementById(closeId);
  if (!modal || !btn) return;
  function close() { modal.classList.remove("open"); }
  btn.addEventListener("click", function() {
    document.querySelectorAll(".modal.open").forEach(m => m.classList.remove("open"));
    modal.classList.add("open");
  });
  closeBtn.addEventListener("click", close);
  modal.addEventListener("click", function(e) {
    if (e.target === modal) close();
  });
}
document.addEventListener("keydown", function(e) {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal.open").forEach(m => m.classList.remove("open"));
  }
});
document.getElementById("methods-content").innerHTML =
  PAYLOAD.methods_html || "<p style='color:#888'>No methods document available.</p>";
document.getElementById("terms-content").innerHTML =
  PAYLOAD.terms_html || "<p style='color:#888'>No terms document available.</p>";
if (PAYLOAD.terms_updated) {
  document.getElementById("terms-updated").textContent = "Last updated: " + PAYLOAD.terms_updated;
}
wireModal("methods-modal", "methods-btn", "methods-close");
wireModal("terms-modal", "terms-btn", "terms-close");

// --- collapsible panels (zone info + layer controls + legend) ---
(function wirePanelToggles() {
  function setCollapsed(panel, btn, collapsed) {
    if (collapsed) {
      panel.classList.add("collapsed");
      btn.textContent = "+";
    } else {
      panel.classList.remove("collapsed");
      btn.textContent = "−";
    }
  }
  const infoPanel = document.getElementById("info");
  const infoBtn = document.getElementById("info-toggle");
  if (infoPanel && infoBtn) {
    infoBtn.addEventListener("click", function() {
      setCollapsed(infoPanel, infoBtn, !infoPanel.classList.contains("collapsed"));
    });
  }
  document.querySelectorAll(".panel-toggle").forEach(function(btn) {
    const panel = document.getElementById(btn.dataset.target);
    if (!panel) return;
    btn.addEventListener("click", function() {
      setCollapsed(panel, btn, !panel.classList.contains("collapsed"));
    });
  });
  if (window.matchMedia && window.matchMedia("(max-width: 700px)").matches) {
    if (infoPanel && infoBtn) setCollapsed(infoPanel, infoBtn, true);
    document.querySelectorAll(".panel-toggle").forEach(function(btn) {
      const panel = document.getElementById(btn.dataset.target);
      if (panel) setCollapsed(panel, btn, true);
    });
  }
})();

// Pre-populate the zone info panel with Mongbwalu.
(function preloadMongbwalu() {
  const target = (TRAVEL_FROM || "Mongbalu").toLowerCase();
  for (const feat of PAYLOAD.geometry.features) {
    if ((feat.properties.name || "").toLowerCase() === target) {
      document.getElementById("info-body").className = "";
      document.getElementById("info-body").innerHTML = infoHTML(feat);
      return;
    }
  }
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"unserializable: {type(o)}")


def main() -> int:
    payload = build_payload()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload_json = json.dumps(payload, separators=(",", ":"), default=_json_default,
                              allow_nan=False)
    html = HTML_TEMPLATE.replace("__PAYLOAD__", payload_json)
    OUTPUT_PATH.write_text(html)
    print(f"\nwrote {OUTPUT_PATH} ({len(html) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
