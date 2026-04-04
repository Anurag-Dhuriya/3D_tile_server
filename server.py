import http.server
import json
import os
import shutil
import threading
import urllib.parse
from datetime import datetime

from pipeline.processing import build_model_artifacts, rebuild_scene, write_bbox_json


DIRECTORY = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIRECTORY, "config.json")
CONFIG_LOCK = threading.RLock()


DEFAULT_CONFIG = {
    "port": 8080,
    "host": "0.0.0.0",
    "models": [],
}


def load_config():
    with CONFIG_LOCK:
        if not os.path.isfile(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)


def save_config(config):
    with CONFIG_LOCK:
        temp_path = CONFIG_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        os.replace(temp_path, CONFIG_PATH)


def find_model(config, name):
    for index, model in enumerate(config.get("models", [])):
        if model["name"] == name:
            return index, model
    return None, None


def update_model_fields(name, **fields):
    config = load_config()
    index, model = find_model(config, name)
    if model is None:
        return None
    config["models"][index].update(fields)
    save_config(config)
    return config["models"][index]


def model_tileset_url(name):
    return f"http://localhost:{PORT}/tiles/{name}/tileset.json"


PATHS = {
    "models_dir": os.path.join(DIRECTORY, "models"),
    "tiles_dir": os.path.join(DIRECTORY, "tiles"),
    "upload_dir": os.path.join(DIRECTORY, "uploads"),
    "lod_dir": os.path.join(DIRECTORY, "lod"),
    "scene_dir": os.path.join(DIRECTORY, "scene"),
}

for path in PATHS.values():
    os.makedirs(path, exist_ok=True)


TOOLS = {
    "blender_path": "/Applications/Blender.app/Contents/MacOS/Blender",
    "blender_script": os.path.join(DIRECTORY, "blender_process.py"),
    "tiles_tools_path": shutil.which("3d-tiles-tools") or "/opt/homebrew/bin/3d-tiles-tools",
}


CONFIG = load_config()
PORT = int(CONFIG.get("port", 8080))
HOST = CONFIG.get("host", "0.0.0.0")


def tool_status_errors():
    errors = []
    if not os.path.isfile(TOOLS["blender_path"]):
        errors.append(f"Blender not found: {TOOLS['blender_path']}")
    if not os.path.isfile(TOOLS["blender_script"]):
        errors.append(f"blender_process.py not found: {TOOLS['blender_script']}")
    if not os.path.isfile(TOOLS["tiles_tools_path"]):
        errors.append(f"3d-tiles-tools not found: {TOOLS['tiles_tools_path']}")
    return errors


def process_model(name):
    config = load_config()
    index, model = find_model(config, name)
    if model is None:
        return False, f"Model not found: {name}"

    for error in tool_status_errors():
        update_model_fields(
            name,
            status="error",
            error=error,
            tileset_url=None,
            processed_at=None,
        )
        return False, error

    update_model_fields(
        name,
        status="processing",
        error=None,
        tileset_url=None,
    )

    try:
        print(f"\n[Server] Processing model: {name}")
        result = build_model_artifacts(model, PATHS, TOOLS)
        write_bbox_json(os.path.join(PATHS["tiles_dir"], name), result["bbox"])
        update_model_fields(
            name,
            status="ready",
            error=None,
            tileset_url=model_tileset_url(name),
            processed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        print(f"[Server] Ready: {model_tileset_url(name)}")
        return True, model_tileset_url(name)
    except Exception as exc:
        update_model_fields(
            name,
            status="error",
            error=str(exc),
            tileset_url=None,
            processed_at=None,
        )
        print(f"[Server] ERROR processing {name}: {exc}")
        return False, str(exc)


def rebuild_scene_from_config():
    config = load_config()
    scene_path = rebuild_scene(config, PATHS)
    if scene_path:
        print(f"[Scene] Scene tileset -> {scene_path}")
        print(f"[Scene] Viewer URL    -> http://localhost:{PORT}/scene/tileset.json")
    else:
        print("[Scene] No valid ready models found for scene tileset")
    return scene_path


def process_all_pending():
    config = load_config()
    pending = [
        model["name"]
        for model in config.get("models", [])
        if model.get("status") in {"pending", "error"}
    ]

    if not pending:
        print("[Server] No pending models to process")
        rebuild_scene_from_config()
        return

    print(f"[Server] Processing {len(pending)} pending model(s)")
    for name in pending:
        process_model(name)

    rebuild_scene_from_config()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        if self.path.endswith(".b3dm"):
            self.send_header("Cache-Control", "public, max-age=3600")
        elif self.path.endswith("tileset.json"):
            self.send_header("Cache-Control", "public, max-age=300")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/api/models":
            self.api_list_models()
            return
        if self.path.startswith("/api/models/") and self.path.endswith("/status"):
            name = self.path.replace("/api/models/", "").replace("/status", "")
            self.api_model_status(name)
            return
        if self.path == "/tilesets":
            self.api_tilesets()
            return
        if self.path.startswith("/tileset/"):
            name = self.path.replace("/tileset/", "").strip("/")
            self.legacy_get_tileset(name)
            return
        if self.path == "/status":
            self.status_page()
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/models":
            self.api_add_model()
            return
        if self.path.startswith("/api/models/") and self.path.endswith("/process"):
            name = self.path.replace("/api/models/", "").replace("/process", "")
            self.api_process_model(name)
            return
        if self.path == "/api/process/all":
            self.api_process_all()
            return
        if self.path == "/api/rebuild/scene":
            self.api_rebuild_scene()
            return
        if self.path == "/upload":
            self.legacy_upload()
            return
        if self.path == "/convert":
            self.legacy_convert()
            return
        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        if self.path.startswith("/api/models/"):
            name = self.path.replace("/api/models/", "").strip("/")
            self.api_update_model(name)
            return
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/models/"):
            name = self.path.replace("/api/models/", "").strip("/")
            self.api_delete_model(name)
            return
        self.send_response(404)
        self.end_headers()

    def api_list_models(self):
        config = load_config()
        self._json(200, {"count": len(config["models"]), "models": config["models"]})

    def api_model_status(self, name):
        config = load_config()
        _, model = find_model(config, name)
        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return
        self._json(200, model)

    def api_tilesets(self):
        config = load_config()
        tilesets = [
            {
                "name": model["name"],
                "tileset_url": model.get("tileset_url"),
                "processed_at": model.get("processed_at"),
            }
            for model in config.get("models", [])
            if model.get("status") == "ready" and model.get("tileset_url")
        ]
        self._json(200, {"count": len(tilesets), "tilesets": tilesets})

    def api_add_model(self):
        body = self._read_body()
        if body is None:
            return

        for field in ["name", "file", "lon", "lat"]:
            if field not in body:
                self._json(400, {"error": f"Missing field: {field}"})
                return

        config = load_config()
        _, existing = find_model(config, body["name"])
        if existing:
            self._json(409, {"error": f"Model exists: {body['name']}"})
            return

        model = {
            "name": body["name"],
            "file": body["file"],
            "unit": body.get("unit", "m"),
            "lon": float(body["lon"]),
            "lat": float(body["lat"]),
            "height": float(body.get("height", 0)),
            "status": "pending",
            "tileset_url": None,
            "error": None,
            "processed_at": None,
        }
        config["models"].append(model)
        save_config(config)
        self._json(201, {"message": f"Added: {model['name']}", "model": model})

    def api_update_model(self, name):
        body = self._read_body()
        if body is None:
            return

        config = load_config()
        index, model = find_model(config, name)
        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return

        for field in ["file", "unit", "lon", "lat", "height"]:
            if field in body:
                config["models"][index][field] = body[field]

        if any(field in body for field in ["file", "unit", "lon", "lat", "height"]):
            config["models"][index]["status"] = "pending"
            config["models"][index]["tileset_url"] = None
            config["models"][index]["error"] = None
            config["models"][index]["processed_at"] = None

        save_config(config)
        self._json(200, {"message": f"Updated: {name}", "model": config["models"][index]})

    def api_delete_model(self, name):
        config = load_config()
        index, model = find_model(config, name)
        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return

        shutil.rmtree(os.path.join(PATHS["tiles_dir"], name), ignore_errors=True)
        shutil.rmtree(os.path.join(PATHS["lod_dir"], name), ignore_errors=True)

        glb_path = os.path.join(PATHS["models_dir"], f"{name}.glb")
        if os.path.isfile(glb_path):
            os.remove(glb_path)

        config["models"].pop(index)
        save_config(config)

        threading.Thread(target=rebuild_scene_from_config, daemon=True).start()
        self._json(200, {"message": f"Deleted: {name}"})

    def api_process_model(self, name):
        config = load_config()
        _, model = find_model(config, name)
        if model is None:
            self._json(404, {"error": f"Model not found: {name}"})
            return

        def run():
            process_model(name)
            rebuild_scene_from_config()

        threading.Thread(target=run, daemon=True).start()
        self._json(
            202,
            {
                "message": f"Processing: {name}",
                "status_url": f"http://localhost:{PORT}/api/models/{name}/status",
            },
        )

    def api_process_all(self):
        def run():
            process_all_pending()

        threading.Thread(target=run, daemon=True).start()
        self._json(
            202,
            {
                "message": "Processing pending models",
                "status_url": f"http://localhost:{PORT}/api/models",
            },
        )

    def api_rebuild_scene(self):
        def run():
            rebuild_scene_from_config()

        threading.Thread(target=run, daemon=True).start()
        self._json(
            202,
            {
                "message": "Rebuilding scene tileset",
                "tileset_url": f"http://localhost:{PORT}/scene/tileset.json",
            },
        )

    def legacy_get_tileset(self, model_name):
        tileset_path = os.path.join(PATHS["tiles_dir"], model_name, "tileset.json")
        if not os.path.isfile(tileset_path):
            self._json(404, {"error": f"Tileset not found: {model_name}"})
            return
        with open(tileset_path, "r", encoding="utf-8") as handle:
            self._json(200, json.load(handle))

    def legacy_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json(400, {"error": "Expected multipart/form-data"})
            return

        boundary = content_type.split("boundary=")[-1].encode()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        fields = self._parse_multipart(raw, boundary)

        obj_data = fields.get("obj_file")
        obj_name = fields.get("obj_filename", b"model.obj").decode()
        unit = fields.get("scale_unit", b"m").decode()
        lon = float(fields.get("lon", b"0").decode())
        lat = float(fields.get("lat", b"0").decode())
        height = float(fields.get("height", b"0").decode())

        if not obj_data:
            self._json(400, {"error": "No file provided"})
            return

        model_name = os.path.splitext(obj_name)[0]
        object_path = os.path.join(PATHS["upload_dir"], obj_name)
        with open(object_path, "wb") as handle:
            handle.write(obj_data)

        config = load_config()
        index, existing = find_model(config, model_name)
        if existing is None:
            config["models"].append(
                {
                    "name": model_name,
                    "file": obj_name,
                    "unit": unit,
                    "lon": lon,
                    "lat": lat,
                    "height": height,
                    "status": "pending",
                    "tileset_url": None,
                    "error": None,
                    "processed_at": None,
                }
            )
        else:
            config["models"][index].update(
                {
                    "file": obj_name,
                    "unit": unit,
                    "lon": lon,
                    "lat": lat,
                    "height": height,
                    "status": "pending",
                    "tileset_url": None,
                    "error": None,
                    "processed_at": None,
                }
            )
        save_config(config)

        ok, result = process_model(model_name)
        rebuild_scene_from_config()
        if not ok:
            self._json(500, {"error": result})
            return

        self._json(
            200,
            {
                "tileset_url": model_tileset_url(model_name),
                "model_name": model_name,
                "scene_url": f"http://localhost:{PORT}/scene/tileset.json",
            },
        )

    def legacy_convert(self):
        body = self._read_body()
        if body is None:
            return

        glb_url = body.get("glb_url", "").strip()
        if not glb_url or not glb_url.startswith("http"):
            self._json(400, {"error": "Valid GLB URL required"})
            return

        model_name = os.path.splitext(
            os.path.basename(urllib.parse.urlparse(glb_url).path)
        )[0]
        file_name = f"{model_name}.glb"
        local_path = os.path.join(PATHS["models_dir"], file_name)
        if not os.path.isfile(local_path):
            self._json(404, {"error": f"File not found: {file_name}"})
            return

        config = load_config()
        index, existing = find_model(config, model_name)
        if existing is None:
            config["models"].append(
                {
                    "name": model_name,
                    "file": file_name,
                    "unit": "m",
                    "lon": float(body.get("lon", 0)),
                    "lat": float(body.get("lat", 0)),
                    "height": float(body.get("height", 0)),
                    "status": "pending",
                    "tileset_url": None,
                    "error": None,
                    "processed_at": None,
                }
            )
        else:
            config["models"][index].update(
                {
                    "file": file_name,
                    "unit": "m",
                    "lon": float(body.get("lon", 0)),
                    "lat": float(body.get("lat", 0)),
                    "height": float(body.get("height", 0)),
                    "status": "pending",
                    "tileset_url": None,
                    "error": None,
                    "processed_at": None,
                }
            )
        save_config(config)

        ok, result = process_model(model_name)
        rebuild_scene_from_config()
        if not ok:
            self._json(500, {"error": result})
            return

        self._json(
            200,
            {
                "tileset_url": model_tileset_url(model_name),
                "model_name": model_name,
                "scene_url": f"http://localhost:{PORT}/scene/tileset.json",
            },
        )

    def status_page(self):
        config = load_config()
        models = config.get("models", [])
        blender_ok = os.path.isfile(TOOLS["blender_path"])
        tiles_tools_ok = os.path.isfile(TOOLS["tiles_tools_path"])

        rows = ""
        for model in models:
            color = {
                "ready": "#5dcaa5",
                "pending": "#EF9F27",
                "processing": "#4a9aba",
                "error": "#e07a7a",
            }.get(model.get("status", "pending"), "#aac8e0")
            name_html = (
                f'<a href="{model["tileset_url"]}">{model["name"]}</a>'
                if model.get("tileset_url")
                else model["name"]
            )
            rows += (
                f"<tr><td>{name_html}</td>"
                f"<td>{model['file']}</td>"
                f"<td style='color:{color}'>{model.get('status', 'pending')}</td>"
                f"<td>{model.get('processed_at') or '—'}</td>"
                f"<td style='color:#e07a7a'>{model.get('error') or '—'}</td></tr>"
            )

        if not rows:
            rows = "<tr><td colspan='5'>No models yet</td></tr>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>3D Tile Server - Status</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body {{ font-family: sans-serif; background: #0d1b2a; color: #aac8e0; padding: 40px; }}
    h1 {{ color: white; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
    th {{ background: #1a2a3a; color: #aac8e0; padding: 10px 14px; text-align: left; }}
    td {{ padding: 10px 14px; border-bottom: 1px solid #1a2a3a; font-size: 13px; }}
    a {{ color: #4a9aba; text-decoration: none; }}
    .scene-box {{ background: #1a2a3a; border: 1px solid #2a3a4a; border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
  </style>
</head>
<body>
  <h1>3D Tile Server</h1>
  <div class="scene-box">
    <strong style="color:white">Scene tileset</strong><br>
    <a href="http://localhost:{PORT}/scene/tileset.json">http://localhost:{PORT}/scene/tileset.json</a>
  </div>
  <h2>Tools</h2>
  <table>
    <tr><th>Tool</th><th>Status</th></tr>
    <tr><td>Blender</td><td>{'Found' if blender_ok else 'Not Found'}</td></tr>
    <tr><td>3d-tiles-tools</td><td>{'Found' if tiles_tools_ok else 'Not Found'}</td></tr>
  </table>
  <h2>Models ({len(models)})</h2>
  <table>
    <thead><tr><th>Name</th><th>File</th><th>Status</th><th>Processed</th><th>Error</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return None

    def _parse_multipart(self, raw, boundary):
        fields = {}
        parts = raw.split(b"--" + boundary)
        for part in parts[1:-1]:
            if b"\r\n\r\n" not in part:
                continue
            header_block, _, body = part.partition(b"\r\n\r\n")
            body = body.rstrip(b"\r\n")
            header_text = header_block.decode(errors="replace")
            name = None
            filename = None
            for line in header_text.splitlines():
                if "Content-Disposition" not in line:
                    continue
                for item in line.split(";"):
                    item = item.strip()
                    if item.startswith("name="):
                        name = item.split("=", 1)[1].strip('"')
                    elif item.startswith("filename="):
                        filename = item.split("=", 1)[1].strip('"')
            if name:
                fields[name] = body
                if filename:
                    fields[name + "_filename"] = filename.encode()
        return fields

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and "favicon" not in str(args[0]):
            print(f"[Server] {args[0]} {args[1]}")


print(f"[Server] Blender        : {TOOLS['blender_path']}")
print(f"[Server] 3d-tiles-tools : {TOOLS['tiles_tools_path']}")
print(f"[Server] Config         : {CONFIG_PATH}")
print("")
print("  3D Tile Server")
print("  --------------")
print(f"  Viewer   : http://localhost:{PORT}/index.html")
print(f"  Status   : http://localhost:{PORT}/status")
print(f"  API      : http://localhost:{PORT}/api/models")
print(f"  Scene    : http://localhost:{PORT}/scene/tileset.json")
print("  Press Ctrl+C to stop")
print("")


threading.Thread(target=process_all_pending, daemon=True).start()

httpd = http.server.HTTPServer((HOST, PORT), Handler)
httpd.serve_forever()
