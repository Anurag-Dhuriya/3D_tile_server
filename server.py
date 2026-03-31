import http.server
import os
import json
import subprocess
import math
import urllib.parse
import shutil
import threading
from datetime import datetime

# ----------------------------------------------------------------
# Load config.json
# ----------------------------------------------------------------
DIRECTORY   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIRECTORY, "config.json")

DEFAULT_CONFIG = {
    "port": 8080,
    "host": "0.0.0.0",
    "models": []
}

def load_config():
    if not os.path.isfile(CONFIG_PATH):
        print(f"[Config] config.json not found — creating default")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

def find_model(config, name):
    for i, m in enumerate(config["models"]):
        if m["name"] == name:
            return i, m
    return None, None

def update_model_status(name, status, tileset_url=None, error=None):
    config = load_config()
    i, model = find_model(config, name)
    if model is None:
        return
    config["models"][i]["status"]      = status
    config["models"][i]["error"]       = error
    config["models"][i]["tileset_url"] = tileset_url
    if status == "ready":
        config["models"][i]["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_config(config)

# ----------------------------------------------------------------
# Setup directories and tool paths
# ----------------------------------------------------------------
config = load_config()

PORT       = config.get("port", 8080)
HOST       = config.get("host", "0.0.0.0")
MODELS_DIR = os.path.join(DIRECTORY, "models")
TILES_DIR  = os.path.join(DIRECTORY, "tiles")
UPLOAD_DIR = os.path.join(DIRECTORY, "uploads")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(TILES_DIR,  exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

BLENDER_PATH     = "/Applications/Blender.app/Contents/MacOS/Blender"
BLENDER_SCRIPT   = os.path.join(DIRECTORY, "blender_process.py")
TILES_TOOLS_PATH = shutil.which("3d-tiles-tools") or "/opt/homebrew/bin/3d-tiles-tools"

print(f"[Server] Blender        : {BLENDER_PATH}")
print(f"[Server] 3d-tiles-tools : {TILES_TOOLS_PATH}")
print(f"[Server] Config         : {CONFIG_PATH}")

# ----------------------------------------------------------------
# Core processing functions
# ----------------------------------------------------------------
def generate_tileset(lon, lat, height, output_folder, width=50, depth=50, model_height=25):
    lon_r = math.radians(lon)
    lat_r = math.radians(lat)
    R     = 6378137 + height

    transform = [
        -math.sin(lon_r),
         math.cos(lon_r),
         0, 0,
        -math.sin(lat_r) * math.cos(lon_r),
        -math.sin(lat_r) * math.sin(lon_r),
         math.cos(lat_r), 0,
         math.cos(lat_r) * math.cos(lon_r),
         math.cos(lat_r) * math.sin(lon_r),
         math.sin(lat_r), 0,
         R * math.cos(lat_r) * math.cos(lon_r),
         R * math.cos(lat_r) * math.sin(lon_r),
         R * math.sin(lat_r), 1
    ]

    hw = width        / 2
    hd = depth        / 2
    hh = model_height / 2

    tileset = {
        "asset": { "version": "1.0" },
        "geometricError": max(width, depth) * 10,
        "root": {
            "transform": transform,
            "boundingVolume": {
                "box": [
                    0, 0, hh,
                    hw, 0, 0,
                    0, hd, 0,
                    0, 0, hh
                ]
            },
            "geometricError": max(width, depth),
            "refine": "ADD",
            "content": { "uri": "tiles/0.b3dm" }
        }
    }

    os.makedirs(os.path.join(output_folder, "tiles"), exist_ok=True)
    tileset_path = os.path.join(output_folder, "tileset.json")
    with open(tileset_path, "w") as f:
        json.dump(tileset, f, indent=2)
    print(f"[Server] tileset.json written → {tileset_path}")


def run_blender(obj_path, glb_path, scale_unit):
    if not os.path.isfile(BLENDER_PATH):
        return False, f"Blender not found at {BLENDER_PATH}"
    if not os.path.isfile(BLENDER_SCRIPT):
        return False, f"blender_process.py not found at {BLENDER_SCRIPT}"

    print(f"[Server] Running Blender headless...")
    print(f"[Server]   Input  : {obj_path}")
    print(f"[Server]   Output : {glb_path}")
    print(f"[Server]   Unit   : {scale_unit}")

    try:
        result = subprocess.run(
            [
                BLENDER_PATH,
                "--background",
                "--python", BLENDER_SCRIPT,
                "--",
                obj_path,
                glb_path,
                scale_unit
            ],
            capture_output=True,
            text=True,
            timeout=300
        )

        for line in result.stdout.splitlines():
            if "[Blender]" in line:
                print(line)

        if result.returncode != 0:
            print(f"[Server] Blender stderr: {result.stderr.strip()}")
            return False, "Blender processing failed: " + result.stderr[:200]

        if not os.path.isfile(glb_path):
            return False, f"Blender ran but GLB was not created at {glb_path}"

        size = os.path.getsize(glb_path)
        print(f"[Server] GLB created — {size/1024:.1f} KB")
        return True, "OK"

    except FileNotFoundError:
        return False, f"Blender not found at {BLENDER_PATH}"
    except subprocess.TimeoutExpired:
        return False, "Blender timed out (5 min limit)"


def run_conversion(glb_path, b3dm_path):
    if not os.path.isfile(TILES_TOOLS_PATH):
        return False, f"3d-tiles-tools not found at {TILES_TOOLS_PATH}"

    os.makedirs(os.path.dirname(b3dm_path), exist_ok=True)

    print(f"[Server] Converting GLB to b3dm...")
    print(f"[Server]   Input  : {glb_path}")
    print(f"[Server]   Output : {b3dm_path}")

    try:
        result = subprocess.run(
            [TILES_TOOLS_PATH, "glbToB3dm",
             "-i", glb_path,
             "-o", b3dm_path,
             "-f"],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.stdout.strip():
            print(f"[Server] 3d-tiles-tools: {result.stdout.strip()}")

        if result.returncode != 0:
            print(f"[Server] 3d-tiles-tools error: {result.stderr.strip()}")
            return False, "Conversion failed: " + result.stderr

        if not os.path.isfile(b3dm_path):
            return False, f"b3dm not created at {b3dm_path}"

        size = os.path.getsize(b3dm_path)
        print(f"[Server] Conversion complete — {size/1024:.1f} KB")
        return True, "OK"

    except FileNotFoundError:
        return False, "3d-tiles-tools not found"
    except subprocess.TimeoutExpired:
        return False, "Conversion timed out"


def process_model(model):
    name   = model["name"]
    file   = model["file"]
    unit   = model.get("unit", "m")
    lon    = float(model.get("lon", 0))
    lat    = float(model.get("lat", 0))
    height = float(model.get("height", 0))

    print(f"\n[Server] ── Processing model: {name} ──")
    update_model_status(name, "processing")

    ext    = os.path.splitext(file)[1].lower()
    is_obj = ext == ".obj"
    is_glb = ext in [".glb", ".gltf"]

    obj_path      = os.path.join(UPLOAD_DIR, file)
    glb_path      = os.path.join(MODELS_DIR, name + ".glb")
    output_folder = os.path.join(TILES_DIR, name)
    b3dm_path     = os.path.join(output_folder, "tiles", "0.b3dm")

    os.makedirs(os.path.join(output_folder, "tiles"), exist_ok=True)

    # Step 1 — Blender for OBJ, direct copy for GLB
    if is_obj:
        if not os.path.isfile(obj_path):
            err = f"OBJ file not found: {obj_path}"
            print(f"[Server] ERROR: {err}")
            update_model_status(name, "error", error=err)
            return False, err

        ok, err = run_blender(obj_path, glb_path, unit)
        if not ok:
            update_model_status(name, "error", error=err)
            return False, err

    elif is_glb:
        glb_source = os.path.join(UPLOAD_DIR, file)
        if not os.path.isfile(glb_source):
            glb_source = os.path.join(MODELS_DIR, file)
        if not os.path.isfile(glb_source):
            err = f"GLB file not found: {file}"
            print(f"[Server] ERROR: {err}")
            update_model_status(name, "error", error=err)
            return False, err
        if glb_source != glb_path:
            shutil.copy2(glb_source, glb_path)
            print(f"[Server] GLB copied to models folder")
    else:
        err = f"Unsupported file format: {ext}"
        update_model_status(name, "error", error=err)
        return False, err

    # Step 2 — Read bounding box from Blender output
    bbox_path              = glb_path.replace('.glb', '_bbox.txt')
    width, depth, model_height = 50, 50, 25
    if os.path.isfile(bbox_path):
        try:
            with open(bbox_path) as f:
                parts        = f.read().strip().split(',')
                width        = float(parts[0])
                depth        = float(parts[1])
                model_height = float(parts[2])
            print(f"[Server] Bounding box: {width:.2f}m × {depth:.2f}m × {model_height:.2f}m")
            os.remove(bbox_path)
        except Exception as e:
            print(f"[Server] Could not read bbox: {e}")

    # Step 3 — Convert GLB to b3dm (always force overwrite)
    ok, err = run_conversion(glb_path, b3dm_path)
    if not ok:
        update_model_status(name, "error", error=err)
        return False, err

    # Step 4 — Generate tileset.json
    print(f"[Server] Generating tileset.json at lon={lon} lat={lat} height={height}")
    generate_tileset(lon, lat, height, output_folder, width, depth, model_height)

    tileset_url = f"http://localhost:{PORT}/tiles/{name}/tileset.json"
    print(f"[Server] Model ready → {tileset_url}\n")

    update_model_status(name, "ready", tileset_url=tileset_url)
    return True, tileset_url


def process_all_pending():
    config  = load_config()
    models  = config.get("models", [])
    pending = [m for m in models if m.get("status") in ["pending", "error"]]

    if not pending:
        print(f"[Server] No pending models to process")
        return

    print(f"[Server] Processing {len(pending)} pending model(s)...")
    for model in pending:
        process_model(model)


def get_all_tilesets():
    config   = load_config()
    tilesets = []
    for model in config.get("models", []):
        if model.get("status") == "ready" and model.get("tileset_url"):
            tilesets.append({
                "name":        model["name"],
                "tileset_url": model["tileset_url"],
                "endpoint":    f"http://localhost:{PORT}/tileset/{model['name']}",
                "created":     model.get("processed_at", "unknown")
            })
    return tilesets


# ----------------------------------------------------------------
# HTTP Handler
# ----------------------------------------------------------------
class Handler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

        if self.path.endswith('.b3dm'):
            self.send_header("Cache-Control", "public, max-age=3600")
        elif self.path.endswith('tileset.json'):
            self.send_header("Cache-Control", "public, max-age=300")
        else:
            self.send_header("Cache-Control", "no-cache")

        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        if self.path == '/api/models':
            self.api_list_models()
            return

        if self.path.startswith('/api/models/') and self.path.endswith('/status'):
            name = self.path.replace('/api/models/', '').replace('/status', '')
            self.api_model_status(name)
            return

        if self.path == '/tilesets':
            self._json(200, {"count": len(get_all_tilesets()), "tilesets": get_all_tilesets()})
            return

        if self.path.startswith('/tileset/'):
            name = self.path.replace('/tileset/', '').strip('/')
            self.legacy_get_tileset(name)
            return

        if self.path == '/status':
            self.legacy_status_page()
            return

        super().do_GET()

    def do_POST(self):
        if self.path == '/api/models':
            self.api_add_model()
            return

        if self.path.startswith('/api/models/') and self.path.endswith('/process'):
            name = self.path.replace('/api/models/', '').replace('/process', '')
            self.api_process_model(name)
            return

        if self.path == '/api/process/all':
            self.api_process_all()
            return

        if self.path == '/upload':
            self.legacy_upload()
            return

        if self.path == '/convert':
            self.legacy_convert()
            return

        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        if self.path.startswith('/api/models/'):
            name = self.path.replace('/api/models/', '').strip('/')
            self.api_update_model(name)
            return
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        if self.path.startswith('/api/models/'):
            name = self.path.replace('/api/models/', '').strip('/')
            self.api_delete_model(name)
            return
        self.send_response(404)
        self.end_headers()

    # ----------------------------------------------------------------
    # API — GET /api/models
    # ----------------------------------------------------------------
    def api_list_models(self):
        config = load_config()
        self._json(200, {
            "count":  len(config["models"]),
            "models": config["models"]
        })
        print(f"[API] GET /api/models — {len(config['models'])} models")

    # ----------------------------------------------------------------
    # API — GET /api/models/{name}/status
    # ----------------------------------------------------------------
    def api_model_status(self, name):
        config   = load_config()
        i, model = find_model(config, name)
        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return
        self._json(200, model)
        print(f"[API] GET /api/models/{name}/status")

    # ----------------------------------------------------------------
    # API — POST /api/models
    # ----------------------------------------------------------------
    def api_add_model(self):
        body = self._read_body()
        if body is None:
            return

        required = ["name", "file", "lon", "lat"]
        for field in required:
            if field not in body:
                self._json(400, {"error": f"Missing required field: {field}"})
                return

        config    = load_config()
        i, exists = find_model(config, body["name"])

        if exists:
            self._json(409, {"error": f"Model already exists: {body['name']}"})
            return

        new_model = {
            "name":         body["name"],
            "file":         body["file"],
            "unit":         body.get("unit", "m"),
            "lon":          float(body["lon"]),
            "lat":          float(body["lat"]),
            "height":       float(body.get("height", 0)),
            "status":       "pending",
            "tileset_url":  None,
            "error":        None,
            "processed_at": None
        }

        config["models"].append(new_model)
        save_config(config)

        print(f"[API] POST /api/models — added: {body['name']}")
        self._json(201, {"message": f"Model added: {body['name']}", "model": new_model})

    # ----------------------------------------------------------------
    # API — PUT /api/models/{name}
    # ----------------------------------------------------------------
    def api_update_model(self, name):
        body = self._read_body()
        if body is None:
            return

        config   = load_config()
        i, model = find_model(config, name)

        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return

        updatable = ["file", "unit", "lon", "lat", "height"]
        for field in updatable:
            if field in body:
                config["models"][i][field] = body[field]

        if any(f in body for f in ["file", "lon", "lat", "height", "unit"]):
            config["models"][i]["status"]       = "pending"
            config["models"][i]["tileset_url"]  = None
            config["models"][i]["error"]        = None
            config["models"][i]["processed_at"] = None

        save_config(config)
        print(f"[API] PUT /api/models/{name} — updated")
        self._json(200, {"message": f"Model updated: {name}", "model": config["models"][i]})

    # ----------------------------------------------------------------
    # API — DELETE /api/models/{name}
    # ----------------------------------------------------------------
    def api_delete_model(self, name):
        config   = load_config()
        i, model = find_model(config, name)

        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return

        tile_folder = os.path.join(TILES_DIR, name)
        if os.path.exists(tile_folder):
            shutil.rmtree(tile_folder)
            print(f"[API] Deleted tile folder: {tile_folder}")

        config["models"].pop(i)
        save_config(config)

        print(f"[API] DELETE /api/models/{name} — removed")
        self._json(200, {"message": f"Model deleted: {name}"})

    # ----------------------------------------------------------------
    # API — POST /api/models/{name}/process
    # ----------------------------------------------------------------
    def api_process_model(self, name):
        config   = load_config()
        i, model = find_model(config, name)

        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return

        def run():
            process_model(model)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        print(f"[API] POST /api/models/{name}/process — started")
        self._json(202, {
            "message":    f"Processing started for: {name}",
            "status_url": f"http://localhost:{PORT}/api/models/{name}/status"
        })

    # ----------------------------------------------------------------
    # API — POST /api/process/all
    # ----------------------------------------------------------------
    def api_process_all(self):
        config  = load_config()
        pending = [m for m in config["models"] if m.get("status") in ["pending", "error"]]

        if not pending:
            self._json(200, {"message": "No pending models to process"})
            return

        def run():
            for model in pending:
                process_model(model)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        print(f"[API] POST /api/process/all — started {len(pending)} model(s)")
        self._json(202, {
            "message":    f"Processing started for {len(pending)} model(s)",
            "models":     [m["name"] for m in pending],
            "status_url": f"http://localhost:{PORT}/api/models"
        })

    # ----------------------------------------------------------------
    # Legacy upload endpoint
    # ----------------------------------------------------------------
    def legacy_upload(self):
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' not in content_type:
            self._json(400, {"error": "Expected multipart/form-data"})
            return

        boundary = content_type.split('boundary=')[-1].encode()
        length   = int(self.headers.get('Content-Length', 0))
        raw      = self.rfile.read(length)
        fields   = self._parse_multipart(raw, boundary)

        obj_file_data = fields.get('obj_file')
        obj_filename  = fields.get('obj_filename', b'model.obj').decode()
        scale_unit    = fields.get('scale_unit', b'm').decode()
        lon           = float(fields.get('lon', b'0').decode())
        lat           = float(fields.get('lat', b'0').decode())
        height        = float(fields.get('height', b'0').decode())

        if not obj_file_data:
            self._json(400, {"error": "No OBJ file provided"})
            return

        model_name = os.path.splitext(obj_filename)[0]
        obj_path   = os.path.join(UPLOAD_DIR, obj_filename)

        with open(obj_path, 'wb') as f:
            f.write(obj_file_data)
        print(f"[Server] OBJ saved: {obj_path} ({len(obj_file_data)/1024:.1f} KB)")

        config    = load_config()
        i, exists = find_model(config, model_name)

        if exists is None:
            config["models"].append({
                "name":         model_name,
                "file":         obj_filename,
                "unit":         scale_unit,
                "lon":          lon,
                "lat":          lat,
                "height":       height,
                "status":       "pending",
                "tileset_url":  None,
                "error":        None,
                "processed_at": None
            })
            save_config(config)
        else:
            config["models"][i]["lon"]    = lon
            config["models"][i]["lat"]    = lat
            config["models"][i]["height"] = height
            config["models"][i]["unit"]   = scale_unit
            config["models"][i]["status"] = "pending"
            save_config(config)

        _, m       = find_model(load_config(), model_name)
        ok, result = process_model(m)

        if not ok:
            self._json(500, {"error": result})
            return

        _, updated = find_model(load_config(), model_name)
        self._json(200, {
            "tileset_url": updated["tileset_url"],
            "model_name":  model_name,
            "dimensions": {
                "width":  50,
                "depth":  50,
                "height": 25
            }
        })

    # ----------------------------------------------------------------
    # Legacy convert endpoint
    # ----------------------------------------------------------------
    def legacy_convert(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)

        try:
            data    = json.loads(body)
            glb_url = data.get('glb_url', '').strip()
            lon     = float(data.get('lon', 0))
            lat     = float(data.get('lat', 0))
            height  = float(data.get('height', 0))
        except Exception as e:
            self._json(400, {"error": "Invalid request: " + str(e)})
            return

        if not glb_url or not glb_url.startswith("http"):
            self._json(400, {"error": "Valid GLB URL required"})
            return

        model_name = os.path.splitext(os.path.basename(
            urllib.parse.urlparse(glb_url).path
        ))[0]

        glb_local = os.path.join(MODELS_DIR, model_name + ".glb")

        if not os.path.isfile(glb_local):
            self._json(404, {"error": f"File not found in models folder: {model_name}.glb"})
            return

        config    = load_config()
        i, exists = find_model(config, model_name)

        if exists is None:
            config["models"].append({
                "name":         model_name,
                "file":         model_name + ".glb",
                "unit":         "m",
                "lon":          lon,
                "lat":          lat,
                "height":       height,
                "status":       "pending",
                "tileset_url":  None,
                "error":        None,
                "processed_at": None
            })
            save_config(config)
        else:
            config["models"][i]["lon"]    = lon
            config["models"][i]["lat"]    = lat
            config["models"][i]["height"] = height
            config["models"][i]["status"] = "pending"
            save_config(config)

        _, m       = find_model(load_config(), model_name)
        ok, result = process_model(m)

        if not ok:
            self._json(500, {"error": result})
            return

        _, updated = find_model(load_config(), model_name)
        self._json(200, {
            "tileset_url": updated["tileset_url"],
            "model_name":  model_name
        })

    # ----------------------------------------------------------------
    # Legacy status page
    # ----------------------------------------------------------------
    def legacy_status_page(self):
        config  = load_config()
        models  = config.get("models", [])
        blender_status = "Found" if os.path.isfile(BLENDER_PATH) else "NOT FOUND"
        tools_status   = "Found" if os.path.isfile(TILES_TOOLS_PATH) else "NOT FOUND"

        rows = ""
        for m in models:
            status_color = {
                "ready":      "#5dcaa5",
                "pending":    "#EF9F27",
                "processing": "#4a9aba",
                "error":      "#e07a7a"
            }.get(m.get("status", "pending"), "#aac8e0")

            tileset_link = f'<a href="{m["tileset_url"]}">{m["tileset_url"]}</a>' \
                           if m.get("tileset_url") else "—"
            error_text   = m.get("error") or "—"

            rows += f"""
            <tr>
                <td>{m['name']}</td>
                <td>{m['file']}</td>
                <td style="color:{status_color}">{m.get('status', 'pending')}</td>
                <td>{tileset_link}</td>
                <td>{m.get('processed_at') or '—'}</td>
                <td style="color:#e07a7a;font-size:11px">{error_text}</td>
            </tr>"""

        if not rows:
            rows = "<tr><td colspan='6'>No models configured yet</td></tr>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>3D Tile Server — Status</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body {{ font-family: sans-serif; background: #0d1b2a; color: #aac8e0; padding: 40px; }}
    h1   {{ color: white; margin-bottom: 4px; }}
    h2   {{ color: #aac8e0; margin: 24px 0 12px; }}
    p    {{ color: #6a9ab0; margin-bottom: 8px; font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
    th   {{ background: #1a2a3a; color: #aac8e0; padding: 10px 14px; text-align: left; border-bottom: 1px solid #2a3a4a; }}
    td   {{ padding: 10px 14px; border-bottom: 1px solid #1a2a3a; font-size: 13px; }}
    a    {{ color: #4a9aba; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .ok  {{ color: #5dcaa5; }}
    .err {{ color: #e07a7a; }}
  </style>
</head>
<body>
  <h1>3D Tile Server</h1>
  <p>Running at http://localhost:{PORT} — auto-refreshes every 5 seconds</p>

  <h2>Tool status</h2>
  <table>
    <tr><th>Tool</th><th>Path</th><th>Status</th></tr>
    <tr>
      <td>Blender</td>
      <td>{BLENDER_PATH}</td>
      <td class="{'ok' if blender_status == 'Found' else 'err'}">{blender_status}</td>
    </tr>
    <tr>
      <td>3d-tiles-tools</td>
      <td>{TILES_TOOLS_PATH}</td>
      <td class="{'ok' if tools_status == 'Found' else 'err'}">{tools_status}</td>
    </tr>
  </table>

  <h2>Models ({len(models)} total)</h2>
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>File</th>
        <th>Status</th>
        <th>Tileset URL</th>
        <th>Processed at</th>
        <th>Error</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>

  <h2>API endpoints</h2>
  <table>
    <tr><th>Method</th><th>Endpoint</th><th>Description</th></tr>
    <tr><td>GET</td>   <td><a href="/api/models">/api/models</a></td>             <td>List all models</td></tr>
    <tr><td>POST</td>  <td>/api/models</td>                                       <td>Add a new model</td></tr>
    <tr><td>PUT</td>   <td>/api/models/{{name}}</td>                              <td>Update a model</td></tr>
    <tr><td>DELETE</td><td>/api/models/{{name}}</td>                              <td>Delete a model</td></tr>
    <tr><td>POST</td>  <td>/api/models/{{name}}/process</td>                      <td>Process one model</td></tr>
    <tr><td>POST</td>  <td>/api/process/all</td>                                  <td>Process all pending</td></tr>
    <tr><td>GET</td>   <td>/api/models/{{name}}/status</td>                       <td>Get model status</td></tr>
  </table>

  <p>
    Viewer: <a href="http://localhost:{PORT}/index.html">http://localhost:{PORT}/index.html</a>
    &nbsp;|&nbsp;
    Tilesets JSON: <a href="http://localhost:{PORT}/tilesets">http://localhost:{PORT}/tilesets</a>
  </p>
</body>
</html>"""

        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ----------------------------------------------------------------
    # Legacy tileset endpoint
    # ----------------------------------------------------------------
    def legacy_get_tileset(self, model_name):
        tileset_path = os.path.join(TILES_DIR, model_name, "tileset.json")
        if not os.path.isfile(tileset_path):
            self._json(404, {"error": f"Tileset not found: {model_name}"})
            return
        with open(tileset_path) as f:
            tileset = json.load(f)
        self._json(200, tileset)

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------
    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        raw    = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            self._json(400, {"error": "Invalid JSON body"})
            return None

    def _parse_multipart(self, raw, boundary):
        fields = {}
        parts  = raw.split(b'--' + boundary)
        for part in parts[1:-1]:
            if b'\r\n\r\n' not in part:
                continue
            header_section, _, body = part.partition(b'\r\n\r\n')
            body    = body.rstrip(b'\r\n')
            headers = header_section.decode(errors='replace')
            name     = None
            filename = None
            for line in headers.splitlines():
                if 'Content-Disposition' in line:
                    for item in line.split(';'):
                        item = item.strip()
                        if item.startswith('name='):
                            name = item.split('=', 1)[1].strip('"')
                        if item.startswith('filename='):
                            filename = item.split('=', 1)[1].strip('"')
            if name:
                fields[name] = body
                if filename:
                    fields[name + '_filename'] = filename.encode()
        return fields

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        if len(args) > 0 and 'favicon' not in str(args[0]):
            print(f"[Server] {args[0]} {args[1]}")


# ----------------------------------------------------------------
# Startup
# ----------------------------------------------------------------
config = load_config()
PORT   = config.get("port", 8080)
HOST   = config.get("host", "0.0.0.0")

print(f"")
print(f"  3D Tile Server")
print(f"  --------------")
print(f"  Viewer   : http://localhost:{PORT}/index.html")
print(f"  Status   : http://localhost:{PORT}/status")
print(f"  API      : http://localhost:{PORT}/api/models")
print(f"  Tilesets : http://localhost:{PORT}/tilesets")
print(f"  Press Ctrl+C to stop")
print(f"")

startup_thread = threading.Thread(target=process_all_pending, daemon=True)
startup_thread.start()

httpd = http.server.HTTPServer((HOST, PORT), Handler)
httpd.serve_forever()