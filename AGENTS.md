# Roadmatch Agent Notes

## Project Overview

Python 3.12+ tool for recovering road network routes from noisy fiber optic detection data. Uses OSMnx for road network data.

## Package Manager

Uses `uv`. Run scripts with:
```bash
uv run python <script.py>
uv run python -m roadmatch <command>
```

## Dual Code Structure

**Current code** (`src/`):
- `src/cli.py`: Minimal CLI, only `fetch-data` subcommand
- `src/mock/`: Data fetcher utilities
- `src/utils/`: Geometry, OSM, types, errors
- `src/roadgraphmodel/`, `src/evaluation/`: Placeholder packages

**Legacy code** (`src/old/`):
- Full CLI: `fetch-data`, `simulate`, `match`, `run-demo`, `evaluate`
- Imported as `roadmatch` package (see below)

### Running the Legacy CLI

The `src/old/` directory IS the `roadmatch` package. Run from `src/`:
```bash
cd src && uv run python -m roadmatch run-demo --config ../configs/demo_shanghai.yaml
```

Or set PYTHONPATH:
```bash
PYTHONPATH=src/old uv run python -m roadmatch <command>
```

## CLI Commands (Legacy)

```bash
roadmatch fetch-data --config configs/demo_shanghai.yaml
roadmatch simulate --config configs/demo_shanghai.yaml --seed 42
roadmatch match --config configs/demo_shanghai.yaml --detections outputs/demo/detections.json
roadmatch run-demo --config configs/demo_shanghai.yaml --seed 42
roadmatch evaluate --config configs/demo_shanghai.yaml --seeds 50
```

## Configuration

YAML files in `configs/`. Key paths:
- `paths.data_dir`: Road network data location
- `paths.output_dir`: Output files location
- `osm.bbox`: `[left, bottom, right, top]` = `[west, south, east, north]`
- `demo.start/end_candidates`: Demo start/end points

## Data Directories

- `resource/`: Cached OSM road network GeoJSON (nodes.geojson, edges.geojson)
- `data/`: Project working data
- `outputs/`: Generated outputs (gitignored)
- `user_data/`: User input data (see `user_data/README.md` for format)

## No Test Framework

`tests/` contains utility scripts (e.g., `plot_road_network.py`), not unit tests. No pytest or similar configured.

## No Linter/Formatter Config

No ruff, flake8, or pre-commit hooks configured.

## Dependencies

Key: `geopandas`, `networkx`, `osmnx`, `plotly`, `pyyaml`, `rustworkx`, `shapely`

## Coordinate System

WGS84. Use `lon, lat` order (not `lat, lon`).