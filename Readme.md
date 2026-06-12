# 3D Tile Server

> FastAPI pipeline for converting CityGML building geometry into 3D Tiles (GLB + `tileset.json`), served for CesiumJS visualization.

---

## Project Status

**Currently implemented up to Phase 6.**

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | FastAPI static tile server + CesiumJS viewer | ✅ Done |
| Phase 2 | CityGML parsing and metadata extraction | ✅ Done |
| Phase 3 | Geometry normalization and mesh generation | ✅ Done |
| Phase 4 | Root tileset skeleton generation | ✅ Done |
| Phase 5 | CityGML building geometry to GLB export | ✅ Done |
| Phase 6 | Valid `tileset.json` generation linked to `root.glb` | ✅ Done |
| Phase 7 | Adaptive spatial tree (quadtree / octree) | 🔲 Next |
| Phase 8 | Multiple GLB tile generation | 🔲 Planned |
| Phase 9 | LOD generation | 🔲 Planned |
| Phase 10 | B3DM support | 🔲 Planned |
| Phase 11 | Texture mapping and material support | 🔲 Planned |
| Phase 12 | Metadata preservation in 3D Tiles | 🔲 Planned |

---

## Tech Stack

| Layer | Library | Version |
|-------|---------|---------|
| Language | Python | 3.12 |
| Framework | FastAPI | 0.115.6 |
| Server | Uvicorn | 0.34.0 |
| XML / CityGML Parsing | lxml | 5.3.0 |
| Coordinate Conversion | pyproj | 3.7.2 |
| Mesh Cleanup | Open3D | 0.19.0 |
| Mesh Processing + GLB Export | trimesh | 4.12.2 |
| Validation / Schemas | Pydantic (pydantic-settings) | 2.7.1 |
| Testing | pytest + httpx | 8.3.4 / 0.28.1 |
| Viewer | CesiumJS | — |
| Numerics | NumPy | 1.26.4 |

---

## Project Structure

```
Tile_Server/
├── app/
│   ├── api/
│   │   └── routes/
│   │       ├── citygml.py
│   │       ├── glb.py
│   │       ├── meshes.py
│   │       └── tilesets.py
│   ├── core/
│   │   └── config.py
│   ├── schemas/
│   │   ├── citygml.py
│   │   ├── glb.py
│   │   ├── mesh.py
│   │   └── tileset.py
│   ├── services/
│   │   ├── citygml_parser.py       # lxml parsing, metadata, polygon extraction
│   │   ├── glb_exporter.py         # trimesh → GLB binary
│   │   ├── mesh_normalizer.py      # triangulation, local-origin, Open3D cleanup
│   │   └── tileset_generator.py    # bounding volume, geometricError, JSON
│   └── main.py
├── data/
│   ├── input/
│   │   └── sample_building.gml
│   └── tilesets/
│       ├── manual/
│       │   └── tileset.json
│       └── generated/
│           └── sample_building/
│               ├── tileset.json
│               └── tiles/
│                   └── root.glb
├── public/
│   └── viewer/
│       ├── index.html
│       ├── app.js
│       └── styles.css
├── tests/
│   └── test_app.py
├── requirements.txt
├── pyproject.toml
└── README.md
```

**Key service files:**

- `app/services/citygml_parser.py` — CityGML parsing, metadata extraction, polygon extraction
- `app/services/mesh_normalizer.py` — polygon validation, triangulation, coordinate normalization
- `app/services/glb_exporter.py` — Open3D mesh cleanup, trimesh GLB export
- `app/services/tileset_generator.py` — bounding volume calculation, `tileset.json` generation
- `public/viewer/app.js` — CesiumJS viewer logic
- `tests/test_app.py` — full test suite (17 tests)

---

## Environment Setup

```bash
cd /Users/anuragdhuriya/Documents/Tile_Server
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Confirm Python version:

```bash
python --version
# Expected: Python 3.12.x
```

### Optional `.env` configuration

```env
APP_NAME="3D Tile Server"
APP_ENV="development"
APP_HOST="127.0.0.1"
APP_PORT="8000"
TILESET_ROOT="data/tilesets"
VIEWER_ROOT="public/viewer"
CITYGML_INPUT_ROOT="data/input"
CESIUM_ION_ACCESS_TOKEN=""
```

Configuration is handled in `app/core/config.py`. Default paths:

- CityGML input: `data/input`
- Tileset output: `data/tilesets`
- Viewer: `public/viewer`

---

## Running the Server

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

- API docs: http://127.0.0.1:8010/docs
- CesiumJS viewer: http://127.0.0.1:8010/

---

## Running Tests

```bash
pytest
```

Expected: **17 passed**

Test coverage includes:

- Health endpoint
- Viewer config endpoint
- Viewer HTML serving
- Static tileset serving
- CityGML file listing
- CityGML metadata extraction
- Mesh normalization
- Root tileset generation
- GLB export
- Complete tileset generation
- Static serving of generated outputs

---

## Quick Start — End-to-End

```bash
# 1. Activate environment
cd /Users/anuragdhuriya/Documents/Tile_Server
source .venv/bin/activate

# 2. Run tests
pytest

# 3. Start server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010

# 4. Generate tileset from sample CityGML
curl -X POST http://127.0.0.1:8010/api/tilesets/from-citygml/sample_building.gml
```

Open in the CesiumJS viewer:

```
http://127.0.0.1:8010/?tilesetUrl=/tiles/generated/sample_building/tileset.json
```

Expected server logs on success:

```
GET /tiles/generated/sample_building/tileset.json 200 OK
HEAD /tiles/generated/sample_building/tiles/root.glb 200 OK
GET /tiles/generated/sample_building/tiles/root.glb 200 OK
```

---

## API Reference

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/viewer-config` | Cesium Ion token + viewer settings |
| `GET` | `/` | CesiumJS viewer HTML |

### CityGML Parsing

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/citygml/files` | List available `.gml` files in `data/input` |
| `GET` | `/api/citygml/analyze/{file_name}` | Extract metadata, bounding boxes, polygon/vertex counts |

Query params for `/analyze`: `include_geometry=true`, `target_crs=EPSG:4326`

Example:
```
http://127.0.0.1:8010/api/citygml/analyze/sample_building.gml
```

### Mesh Generation

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/meshes/from-citygml/{file_name}` | Generate normalized mesh JSON |

Query params: `target_crs=`, `local_origin=true`, `include_geometry=true`

Example:
```
http://127.0.0.1:8010/api/meshes/from-citygml/sample_building.gml
```

### Root Tileset Skeleton

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tilesets/root-from-citygml/{file_name}` | Root `tileset.json` without `content.uri` |

Returns a 3D Tiles structure with `asset.version`, `geometricError`, `root.boundingVolume.box`, and `root.refine = REPLACE`.

### GLB Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/glb/from-citygml/{file_name}` | Generate `root.glb` from CityGML |
| `GET` | `/api/glb/status/{dataset_name}` | Check GLB export status |

```bash
curl -X POST http://127.0.0.1:8010/api/glb/from-citygml/sample_building.gml
```

Output: `data/tilesets/generated/sample_building/tiles/root.glb`

### Complete Tileset Generation

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tilesets/from-citygml/{file_name}` | Full pipeline — generates `tileset.json` + `root.glb` |
| `GET` | `/api/tilesets/status/{dataset_name}` | Check generated tileset status |

```bash
curl -X POST http://127.0.0.1:8010/api/tilesets/from-citygml/sample_building.gml
```

Output:
- `data/tilesets/generated/sample_building/tileset.json`
- `data/tilesets/generated/sample_building/tiles/root.glb`

### Static File Serving

FastAPI serves `data/tilesets/` at `/tiles/`.

| URL | File |
|-----|------|
| `http://127.0.0.1:8010/tiles/generated/sample_building/tileset.json` | Generated tileset |
| `http://127.0.0.1:8010/tiles/generated/sample_building/tiles/root.glb` | Generated GLB |

---

## Generated `tileset.json` Format

```json
{
  "asset": {
    "version": "1.1"
  },
  "geometricError": 24.49489742783178,
  "root": {
    "boundingVolume": {
      "box": [
        5.0, 5.0, 10.0,
        5.0, 0.0, 0.0,
        0.0, 5.0, 0.0,
        0.0, 0.0, 10.0
      ]
    },
    "geometricError": 0.0,
    "refine": "REPLACE",
    "content": {
      "uri": "tiles/root.glb"
    }
  }
}
```

**`boundingVolume.box` layout:** `[cx, cy, cz, hx_x, hx_y, hx_z, hy_x, hy_y, hy_z, hz_x, hz_y, hz_z]`

For the sample dataset:
- Bounding box: min = `[0, 0, 0]`, max = `[10, 10, 20]`
- Center: `[5, 5, 10]`, half-sizes: `[5, 5, 10]`
- `geometricError = sqrt(10² + 10² + 20²) = 24.495`

---

## Processing Pipeline

```
CityGML input
  → lxml XML parsing
  → metadata extraction
  → polygon extraction
  → raw mesh object creation
  → polygon validation
  → triangulation (fan)
  → local-origin normalization
  → Open3D mesh cleanup
  → trimesh processing
  → GLB export
  → tileset.json generation
  → FastAPI static serving
  → CesiumJS visualization
```

> **Note:** Fan triangulation is used. Complex concave polygons may produce incorrect mesh topology and may need ear-clipping or a more robust triangulator.

---

## Sample Dataset Metrics

For `sample_building.gml`:

| Metric | Value |
|--------|-------|
| Dataset name | `sample_building` |
| Mesh count | 1 |
| Vertex count | 6 |
| Triangle count | 4 |
| GLB output | `data/tilesets/generated/sample_building/tiles/root.glb` |
| Tileset output | `data/tilesets/generated/sample_building/tileset.json` |

---

## Adding a New Feature

Follow this pattern for every new feature:

```
1. app/schemas/{feature}.py          → Pydantic request/response models
2. app/services/{feature}_service.py → Business logic (parsing, I/O, transformation)
3. app/api/routes/{feature}.py       → FastAPI router with endpoints
4. app/main.py                       → Register router: app.include_router(...)
5. tests/test_app.py                 → Add test coverage
```

Current routers registered in `app/main.py`:

```python
app.include_router(citygml_router)
app.include_router(meshes_router)
app.include_router(tilesets_router)
app.include_router(glb_router)
```

---

## Known Limitations

- Simple CityGML building geometry only — no complex LOD or feature types
- Fan triangulation — concave polygons may produce incorrect mesh topology
- Textures detected from CityGML but not baked into GLB output
- No adaptive spatial tree (quadtree / octree) — single root tile only
- No multi-tile generation
- No LOD generation
- No B3DM export
- No user upload UI
- Local filesystem storage only

---

## Recommended Next Phases

```
Phase 7  → Adaptive spatial tree (quadtree or octree) for large datasets
Phase 8  → Multiple GLB tile generation from the spatial tree
Phase 9  → LOD generation per tile
Phase 10 → B3DM format support alongside GLB
Phase 11 → Texture mapping and material support in GLB output
Phase 12 → Metadata preservation inside 3D Tiles
```

---

## Git Notes

Recommended `.gitignore`:

```gitignore
.venv/
.venv-py314-backup/
__pycache__/
*.py[cod]
.pytest_cache/
.env
.DS_Store
```

Generated outputs can be regenerated via API calls. For a source-only repo, do not commit `.venv/`, `.env`, `__pycache__/`, or `.pytest_cache/`.