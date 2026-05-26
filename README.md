# Ebola Bundibugyo DRC 2026 — Dashboard

![Logos for Project Lead Organizations: Institute National de Recherche Biomedicale (INRB), One Health Institute for Africa (INOHA), Institut National de Santé Publique (INSP), Unité de Modélisation et Intelligence Epidémique (UMIE), and AfricaCDC](https://scarpino.github.io/files/drc_logos.png)

## Contributors
This work is led by the Institut National de Recherche Biomédicale (INRB) Kinshasa/One Health Institute for Africa (INOHA) Kinshasa (Dav Ebengo, Placide Mbala-Kingebeni and Tania Bishola), and the Institut National de Santé Publique (INSP) (Pierre Akilimali, Adelard Lofungola) in collaboration with the AfricaCDC and partners across the University of Oxford and Northeastern University; please contact dav.ebengo@umie-inrb.org or pierre.akilimali@insp.cd for further information.

## Data
- **Geo-Harmonized Data**: [Global.health](https://github.com/kraemer-lab/Ebola_DRC_2026)
 - **Epidemiological Data**: [World Health Organization](https://iris.who.int/server/api/core/bitstreams/bb1d4668-04e0-4563-b7c4-d1bdefbc9f05/content)
 - **DRC health zones**: [Humanitarian Data Exchange](https://data.humdata.org/dataset/drc-health-data) (MoH zones de santé shapefile)
- **Population raster**: [GRID3 v4.4 gridded population](https://data.grid3.org/maps/a3db539c0fae4c05aed92ed67e11fe2b/about)
- **Health facilities**: [GRID3 COD Health Facilities v8.0](https://data.grid3.org/datasets/GRID3::grid3-cod-health-facilities-v8-0/about)
- **Refugee / IDP sites**: [OpenStreetMap](https://www.openstreetmap.org/) (via Overpass API)
- **Flowminder**: [Flowminder](https://www.flowminder.org/) 
- **Displaced Movement Outputs**: [IOM](https://dtm.iom.int/)
- **Epidemiological Data**: [INSP](https://insp.cd/category/sitrep/)
- **health_zone_metadata.csv** Metadata file for dashboard, see below. 
- **Methods** Methods for dashboard, see below.
- **ToS** ToS for dashboard, see below. 

## Repository layout relevant for building dashboard
```
EBOV2026_Epidemic_Dashboard/
├── Scripts/
│   ├── refresh.py                  # end-to-end pipeline orchestrator
│   ├── fetch_insp_sitrep.py        # downloads + parses a single INSP sitrep PDF
│   ├── backfill_insp_sitreps.py    # iterates the URL pattern to catch up
│   ├── merge_sitrep_into_metadata.py # merges new INSP sitrep data into metadata file
│   └── build_dashboard_public.py   # renders Data/ → output/dashboard.html
├── Data/
│   ├── health_zone_metadata.csv    # one row per DRC health zone
│   ├── DRC Health Zones/<*.shp>    # MoH zones de santé shapefile
│   ├── Epidemiological Data/
│   │   ├── YYYY-MM-DD.csv          # one CSV per parsed INSP sitrep
│   │   ├── pdfs/                   # raw PDFs archived by sitrep number
│   │   └── latest_sitrep.json      # pointer to the most recent sitrep
│   ├── Methods/Contributors_Methods_Data_website.docx # website information on contributors, methods, and data. 
│   ├── ToS/Terms of Use.txt # website information on ToS
│   ├── Branding/                   # partner logos + urls.txt
├── output/
│   └── dashboard.html              # build artefact (self-contained, ~3.6 MB)
└── index.html                      # publicly served copy
```

## Setup
Dependencies: Python 3.10+ with `pandas`, `pypdf`, `python-docx`, `fiona`,
`shapely`, `numpy`. The `fiona`/`shapely` stack pulls in GDAL, which is
easiest to install via conda-forge:

```bash
conda create -n ebov2026 -c conda-forge python=3.12 \
    pandas pypdf python-docx fiona shapely numpy
conda activate ebov2026
```

(pip should work too, but you may need system GDAL headers for `fiona` on macOS/Linux.)

## Building the dashboard

```bash
# Standard refresh: check for new INSP sitreps, merge, rebuild.
python Scripts/refresh.py

# Force a rebuild even when no new sitrep is available
# (useful when health_zone_metadata.csv changed for other reasons).
python Scripts/refresh.py --force-rebuild

# Skip individual steps:
python Scripts/refresh.py --skip-fetch    # merge + rebuild only
python Scripts/refresh.py --skip-rebuild  # fetch + merge only
```

The pipeline writes the processed CSV + raw PDF into `Data/Epidemiological Data/`,
updates `Data/health_zone_metadata.csv` in place, and produces a single
self-contained `output/dashboard.html` that embeds all geometry, per-zone
aggregates, Methods text, Terms of Use, and partner logos.

`Data/` location can be overridden with the `DATA_ROOT` environment
variable (useful when testing from a different working directory):

```bash
DATA_ROOT=/path/to/Data python Scripts/build_dashboard_public.py
```

## Citation
Please cite the original data providers (links above) and this repository if any code or derived data is reused.

INRB/INOHA/INSP DRC Data Dashboard DOI: 10.5281/zenodo.20347624

## Additional license, warranty, and copyright information
We provide a license for our code (see LICENSE) and do not claim ownership, nor the right to license, the data we have obtained nor any third-party software tools/code used in our analyses.  Please cite the appropriate agency, paper, and/or individual in publications and/or derivatives using these data, contact them regarding the legal use of these data, and remember to pass-forward any existing license/warranty/copyright information.  As a reminder (even though you are not supposed to copy/use anything in this repository), should you violate our license, THE DATA AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE DATA AND/OR SOFTWARE OR THE USE OR OTHER DEALINGS IN THE DATA AND/OR SOFTWARE.
