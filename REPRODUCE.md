# Offline Stage 2 reproduction

This project reproduces the accepted Stage 2 cleaning, analysis and reporting outputs from the preserved Comtrade raw responses. The pipeline does not make network requests. It requires Python 3.12 and the pinned plotting environment in `requirements-plot.txt`; the analysis and data-cleaning steps use only the Python standard library.

Create a virtual environment and install the plotting requirements once, while connected to your package source:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-plot.txt
```

Run the complete pipeline from the project root:

```sh
python src/resilience/reproduce.py run --root .
```

This command reads `data/raw/`, runs the data quality gate, rebuilds the processed Stage 2 tables and `results/stage2/data_quality.md`, regenerates the accepted analysis tables, and creates figures and paired CSV/Markdown report tables. It checks hashes of every raw file before and after the run. It does not fetch or refresh source data.

Verify the preserved outputs by running the same pipeline inside a temporary public copy:

```sh
python src/resilience/reproduce.py verify --root .
```

Verification constructs a minimal public replay copy with no `.agent-work` or `.git` and passes the copy's root explicitly to each command. The replay checks the analysis and report manifests, compares the regenerated quality report, processed tables and analysis outputs byte-for-byte, compares decoded RGBA PNG pixels channel by channel (including RGB values under zero alpha), and compares SVGs, tables and manifests byte-for-byte. It also hashes all raw inputs before and after replay. `verify` does not rewrite accepted outputs in the original project.

The two frozen manifests are `results/stage2/analysis/output_manifest.json` and `results/stage2/tables/report_manifest.json`. The report artifact list and figure captions are in `results/stage2/tables/INDEX.md`. Figures include both PNG and SVG versions. Table CSVs preserve accepted values; Markdown files provide a readable view. The figure captions state denominators, units, scenario assumptions and interpretation limits.

The PNG check establishes RGBA pixel identity under the recorded plotting environment, not merely equal numeric source data. A different Matplotlib, font or rendering stack may yield different image bytes or pixels even when the underlying numbers agree; retain the environment record and inspect any such difference before describing images as equivalent.
