import http.server
import os
import json
import subprocess
import math
import urllib.parse
from datetime import datetime

PORT = 8080
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(DIRECTORY, "models")
TILES_DIR  = os.path.join(DIRECTORY, "tiles")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(TILES_DIR,  exist_ok=True)


def generate_tileset(lon, lat, height, output_folder):
    lon_r = math.radians(lon)
    lat_r = math.radians(lat)
    R = 6378137 + height

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

    tileset = {
        "asset": { "version": "1.0" },
        "geometricError": 500,
        "root": {
            "transform": transform,
            "boundingVolume": {
                "box": [0, 0, 25, 50, 0, 0, 0, 50, 0, 0, 0, 25]
            },
            "geometricError": 100,
            "refine": "ADD",
            "content": { "uri": "tiles/0.b3dm" }
        }
    }

    os.makedirs(os.path.join(output_folder, "tiles"), exist_ok=True)
    with open(os.path.join(output_folder, "tileset.json"), "w") as f:
        json.dump(tileset, f, indent=2)

    print(f"[Server] tileset.json written to {output_folder}")


def get_all_tilesets():
    tilesets = []
    if not os.path.exists(TILES_DIR):
        return tilesets
    for name in os.listdir(TILES_DIR):
        tileset_path = os.path.join(TILES_DIR, name, "tileset.json")
        if os.path.isfile(tileset_path):
            modified = os.path.getmtime(tileset_path)
            created  = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M:%S")
            tilesets.append({
                "name":        name,
                "tileset_url": f"http://localhost:{PORT}/tiles/{name}/tileset.json",
                "endpoint":    f"http://localhost:{PORT}/tileset/{name}",
                "created":     created
            })
    return tilesets


class Handler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        # Suppress favicon
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        # Endpoint 1 — list all tilesets as JSON
        if self.path == '/tilesets':
            self.handle_list_tilesets()
            return

        # Endpoint 2 — serve individual tileset.json by name
        if self.path.startswith('/tileset/'):
            model_name = self.path.replace('/tileset/', '').strip('/')
            self.handle_get_tileset(model_name)
            return

        # Endpoint 3 — status page
        if self.path == '/status':
            self.handle_status_page()
            return

        super().do_GET()

    def do_POST(self):
        if self.path == '/convert':
            self.handle_convert()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_list_tilesets(self):
        tilesets = get_all_tilesets()
        self._json(200, {
            "count":    len(tilesets),
            "tilesets": tilesets
        })
        print(f"[Server] Listed {len(tilesets)} tilesets")

    def handle_get_tileset(self, model_name):
        tileset_path = os.path.join(TILES_DIR, model_name, "tileset.json")
        if not os.path.isfile(tileset_path):
            self._json(404, {"error": f"Tileset not found: {model_name}"})
            return
        with open(tileset_path) as f:
            tileset = json.load(f)
        self._json(200, tileset)
        print(f"[Server] Served tileset for {model_name}")

    def handle_status_page(self):
        tilesets = get_all_tilesets()

        rows = ""
        for t in tilesets:
            rows += f"""
            <tr>
                <td>{t['name']}</td>
                <td><a href="{t['endpoint']}">{t['endpoint']}</a></td>
                <td><a href="{t['tileset_url']}">{t['tileset_url']}</a></td>
                <td>{t['created']}</td>
            </tr>"""

        if not rows:
            rows = "<tr><td colspan='4'>No tilesets loaded yet</td></tr>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>3D Tile Server — Status</title>
  <style>
    body {{ font-family: sans-serif; background: #0d1b2a; color: #aac8e0; padding: 40px; }}
    h1 {{ color: white; margin-bottom: 8px; }}
    p {{ color: #6a9ab0; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ background: #1a2a3a; color: #aac8e0; padding: 10px 14px; text-align: left; border-bottom: 1px solid #2a3a4a; }}
    td {{ padding: 10px 14px; border-bottom: 1px solid #1a2a3a; color: #aac8e0; }}
    a {{ color: #4a9aba; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .count {{ background: #1a4a6a; color: white; padding: 4px 10px; border-radius: 12px; font-size: 13px; }}
  </style>
</head>
<body>
  <h1>3D Tile Server</h1>
  <p>Running at http://localhost:{PORT}</p>
  <h2>Available tilesets <span class="count">{len(tilesets)}</span></h2>
  <table>
    <thead>
      <tr>
        <th>Model name</th>
        <th>Tileset endpoint</th>
        <th>Direct tileset.json</th>
        <th>Created</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  <br>
  <p>
    Viewer: <a href="http://localhost:{PORT}/index.html">http://localhost:{PORT}/index.html</a> &nbsp;|&nbsp;
    All tilesets JSON: <a href="http://localhost:{PORT}/tilesets">http://localhost:{PORT}/tilesets</a>
  </p>
</body>
</html>"""

        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print(f"[Server] Status page served")

    def handle_convert(self):
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

        if not glb_url:
            self._json(400, {"error": "Please provide a GLB URL"})
            return

        if not glb_url.startswith("http"):
            self._json(400, {"error": "URL must start with http:// or https://"})
            return

        model_name    = os.path.splitext(os.path.basename(
                            urllib.parse.urlparse(glb_url).path
                        ))[0]
        output_folder = os.path.join(TILES_DIR, model_name)
        b3dm_path     = os.path.join(output_folder, "tiles", "0.b3dm")

        os.makedirs(os.path.join(output_folder, "tiles"), exist_ok=True)

        glb_local = os.path.join(MODELS_DIR, model_name + ".glb")

        if not os.path.isfile(glb_local):
            print(f"[Server] File not found: {glb_local}")
            self._json(404, {"error": f"File not found in models folder: {model_name}.glb"})
            return

        size_mb = os.path.getsize(glb_local) / (1024 * 1024)
        print(f"[Server] Found {model_name}.glb — {size_mb:.2f} MB")

        try:
            print(f"[Server] Converting {model_name}.glb to b3dm ...")
            result = subprocess.run(
                ["/opt/homebrew/bin/3d-tiles-tools", "glbToB3dm",
                 "-i", glb_local,
                 "-o", b3dm_path],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode != 0:
                print(f"[Server] Conversion failed: {result.stderr}")
                self._json(500, {"error": "Conversion failed: " + result.stderr})
                return
            print(f"[Server] Conversion complete")
        except FileNotFoundError:
            print("[Server] 3d-tiles-tools not found at /opt/homebrew/bin/")
            self._json(500, {"error": "3d-tiles-tools not found. Run: npm install -g 3d-tiles-tools"})
            return
        except subprocess.TimeoutExpired:
            print("[Server] Conversion timed out after 120 seconds")
            self._json(500, {"error": "Conversion timed out — try a smaller file"})
            return

        print(f"[Server] Generating tileset.json at lon={lon} lat={lat} height={height} ...")
        generate_tileset(lon, lat, height, output_folder)

        tileset_url = f"http://localhost:{PORT}/tiles/{model_name}/tileset.json"
        print(f"[Server] Done — {tileset_url}\n")

        self._json(200, {
            "tileset_url": tileset_url,
            "model_name":  model_name
        })

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


print(f"")
print(f"  3D Tile Server")
print(f"  --------------")
print(f"  Viewer   : http://localhost:{PORT}/index.html")
print(f"  Status   : http://localhost:{PORT}/status")
print(f"  Tilesets : http://localhost:{PORT}/tilesets")
print(f"  Models   : http://localhost:{PORT}/models/")
print(f"  Tiles    : http://localhost:{PORT}/tiles/")
print(f"  Press Ctrl+C to stop")
print(f"")

httpd = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
httpd.serve_forever()
