# Ebola Bundibugyo DRC 2026 — Dashboard

![Logos for Project Lead Organizations: Institute National de Recherche Biomedicale (INRB), One Health Institute for Africa (INOHA), Institut National de Santé Publique (INSP), Unité de Modélisation et Intelligence Epidémique (UMIE), and AfricaCDC](https://github.com/INRB-UMIE/EBOV2026_Epidemic_Dashboard/blob/main/Data/Branding/all_logos.png)

## Contributors
This work is led by the Institut National de Santé Publique (INSP) (Pierre Akilimali, Adelard Lofungola) and the Institut National de Recherche Biomédicale (INRB) Kinshasa/One Health Institute for Africa (INOHA) Kinshasa (Dav Ebengo, Placide Mbala-Kingebeni and Tania Bishola) in collaboration with the AfricaCDC and partners across the University of Oxford and Northeastern University; please contact dav.ebengo@umie-inrb.org or pierre.akilimali@insp.cd for further information.

## Data
- **health_zone_metadata.csv** Metadata file for dashboard, see below. 
- **Methods** Methods for dashboard, see below.
- **ToS** ToS for dashboard, see below. 

## Repository layout
```
EBOV2026_Epidemic_Dashboard/
├── Scripts/
│   └── build_dashboard_public.py   # builds output/dashboard.html
├── Data/
│   ├── health_zone_metadata.csv    # fallback fields (relative risk, population bounds, etc.)
│   ├── Methods/Contributors_Methods_Data_website.docx
│   ├── ToS/Terms of Use.txt
│   └── Branding/                   # partner logos + urls.txt
├── output/
│   └── dashboard.html              # build artefact (self-contained, ~4 MB)
└── index.html                      # publicly served copy
```

## Prerequisites

**Required companion repo:** The build script reads geometry, epidemiological
data, population, health facility, OSRM travel-time, IDP, and Flowminder
data from the [Ebola_DRC_2026](https://github.com/INRB-UMIE/Ebola_DRC_2026)
repository. It must be cloned as a sibling directory:

```
inrb/
├── Ebola_DRC_2026/          # companion repo (must be built first)
│   ├── build/
│   │   ├── drc_health_zones.geojson   # zone polygons + embedded properties
│   │   └── long/                      # OSRM matrices, etc.
│   └── data/
│       ├── IDP/processed/             # displacement matrices
│       └── flowminder/processed/      # mobility matrices
└── EBOV2026_Epidemic_Dashboard/       # this repo
```

Run the Ebola_DRC_2026 build pipeline first so that `build/` is populated.

## Setup

Dependencies: Python 3.10+ with `pandas`, `python-docx`, `shapely`, `numpy`.

```bash
conda create -n ebov2026 -c conda-forge python=3.12 \
    pandas python-docx shapely numpy
conda activate ebov2026
```

## Building the dashboard

```bash
python Scripts/build_dashboard_public.py
```

This produces a single self-contained `output/dashboard.html` that embeds
all geometry, per-zone aggregates, Methods text, Terms of Use, and partner
logos.

Override default paths with environment variables if the repos are not
sibling directories:

```bash
BUILD_DIR=/path/to/Ebola_DRC_2026/build \
DATA_ROOT=/path/to/Data \
python Scripts/build_dashboard_public.py
```

## Citation
Please cite the original data providers (links above) and this repository if any code or derived data is reused.

INRB/INOHA/INSP DRC Data Dashboard DOI: 10.5281/zenodo.20347624

## Additional license, warranty, and copyright information
We provide a license for our code (see LICENSE) and do not claim ownership, nor the right to license, the data we have obtained nor any third-party software tools/code used in our analyses.  Please cite the appropriate agency, paper, and/or individual in publications and/or derivatives using these data, contact them regarding the legal use of these data, and remember to pass-forward any existing license/warranty/copyright information.  As a reminder (even though you are not supposed to copy/use anything in this repository), should you violate our license, THE DATA AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE DATA AND/OR SOFTWARE OR THE USE OR OTHER DEALINGS IN THE DATA AND/OR SOFTWARE.
