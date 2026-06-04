# Roadmatch Agent Notes

## Project Overview

Python 3.12+ tool for recovering road network routes from noisy fiber optic detection data.

## Running Code

```bash
# Visualization scripts in tests/
uv run python tests/plot_c_graph.py --data-dir resource/miniquad

# CLI (fetch-data only)
cd src && uv run python cli.py fetch-data --city shanghai --bbox 121.4 31.2 121.5 31.3

# Legacy CLI (archived, do not modify)
cd src && uv run python -m old run-demo --config ../configs/demo_shanghai.yaml
```

## Code Structure

- `src/mock/`: Data fetcher, edge splitter, graph simplifier (C-edge clustering)
- `src/utils/`: Geometry, OSM helpers, types, errors
- `src/old/`: Legacy code archive — do not modify, only reference
- `tests/`: Visualization scripts, not unit tests

## Key Conventions

- **Coordinates**: WGS84, `lon, lat` order (not `lat, lon`)
- **No test framework**: `tests/` contains plot scripts, no pytest
- **No linter/formatter**: No ruff, flake8, or pre-commit configured
- **Commit messages**: 使用中文撰写 commit message

## Data Directories

- `resource/<city>/`: OSM road network GeoJSON (`nodes.geojson`, `edges.geojson`, `raw/`)
- `configs/`: YAML config files for legacy CLI
- `outputs/`: Generated outputs (gitignored)