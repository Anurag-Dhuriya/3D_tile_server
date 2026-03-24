import math
import json
import os

# --- Set your values here ---
LON    = 72.5714   # your building longitude
LAT    = 23.0225   # your building latitude
HEIGHT = 0         # meters above ground (increase if building appears underground)

lon_r = math.radians(LON)
lat_r = math.radians(LAT)
R = 6378137 + HEIGHT

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
    "asset": {
        "version": "1.0"
    },
    "geometricError": 500,
    "root": {
        "transform": transform,
        "boundingVolume": {
            "box": [
                0, 0, 0,
                50, 0, 0,
                0, 50, 0,
                0, 0, 50
            ]
        },
        "geometricError": 100,
        "refine": "ADD",
        "content": {
            "uri": "tiles/0.b3dm"
        }
    }
}

os.makedirs("output_tiles", exist_ok=True)
with open("output_tiles/tileset.json", "w") as f:
    json.dump(tileset, f, indent=2)

print("tileset.json created successfully")
print(f"Building placed at lon={LON}, lat={LAT}, height={HEIGHT}m")