import json
import math
import os

from .geo import east_north_up_transform, meters_to_lat_delta, meters_to_lon_delta
from .quadtree import build_quadtree


LOD_LEVELS = ["lod0", "lod1", "lod2", "lod3"]


def model_lod_errors(bbox):
    size = max(
        float(bbox.get("width", 0.0)),
        float(bbox.get("depth", 0.0)),
        float(bbox.get("height", 0.0)),
        1.0,
    )
    return {
        "lod0": max(20.0, size * 4.0),
        "lod1": max(5.0, size * 1.5),
        "lod2": max(1.0, size * 0.5),
        "lod3": 0.0,
    }


def make_box_bounding_volume(bbox):
    width = float(bbox.get("width", 20.0))
    depth = float(bbox.get("depth", 20.0))
    height = float(bbox.get("height", 10.0))

    half_w = width / 2.0
    half_d = depth / 2.0
    half_h = height / 2.0

    return {
        "box": [
            0.0, 0.0, half_h,
            half_w, 0.0, 0.0,
            0.0, half_d, 0.0,
            0.0, 0.0, half_h,
        ]
    }


def build_model_tileset(output_folder, b3dm_map, bbox, lon, lat, height):
    errors = model_lod_errors(bbox)
    bounding_volume = make_box_bounding_volume(bbox)

    def make_node(level_index):
        level = LOD_LEVELS[level_index]
        content_path = b3dm_map.get(level)
        if not content_path:
            return None

        node = {
            "boundingVolume": bounding_volume,
            "geometricError": errors[level],
            "refine": "REPLACE",
            "content": {
                "uri": os.path.relpath(content_path, output_folder).replace("\\", "/")
            },
        }

        next_index = level_index + 1
        if next_index < len(LOD_LEVELS):
            child = make_node(next_index)
            if child:
                node["children"] = [child]

        return node

    root = make_node(0)
    if root is None:
        return None

    root["transform"] = east_north_up_transform(lon, lat, height)

    tileset = {
        "asset": {"version": "1.0"},
        "geometricError": errors["lod0"] * 2.0,
        "root": root,
    }

    os.makedirs(output_folder, exist_ok=True)
    tileset_path = os.path.join(output_folder, "tileset.json")
    with open(tileset_path, "w", encoding="utf-8") as handle:
        json.dump(tileset, handle, indent=2)
    return tileset_path


def _scene_model_region(model):
    bbox = model.get("_bbox") or {}
    width = float(bbox.get("width", 20.0))
    depth = float(bbox.get("depth", 20.0))
    height = float(bbox.get("height", 20.0))
    lon = float(model["lon"])
    lat = float(model["lat"])
    base_height = float(model.get("height", 0.0))

    lon_delta = meters_to_lon_delta(max(width, 2.0) / 2.0, lat)
    lat_delta = meters_to_lat_delta(max(depth, 2.0) / 2.0)

    return {
        "region": [
            math.radians(lon - lon_delta),
            math.radians(lat - lat_delta),
            math.radians(lon + lon_delta),
            math.radians(lat + lat_delta),
            base_height,
            base_height + max(height, 10.0),
        ]
    }


def build_scene_tileset(scene_dir, tiles_dir, ready_models, max_depth=4, max_per_cell=4):
    valid_models = [
        model for model in ready_models
        if os.path.isfile(os.path.join(tiles_dir, model["name"], "tileset.json"))
    ]
    if not valid_models:
        return None

    tree = build_quadtree(valid_models, max_depth=max_depth, max_per_cell=max_per_cell)
    if tree is None:
        return None

    leaves = tree.leaves()
    if not leaves:
        return None

    scene_root_error = max(200.0, len(valid_models) * 40.0)
    cell_error = max(80.0, scene_root_error / 2.0)

    scene_children = []
    for leaf in leaves:
        model_children = []
        for model in leaf.models:
            model_tileset = os.path.join(tiles_dir, model["name"], "tileset.json")
            if not os.path.isfile(model_tileset):
                continue
            model_children.append({
                "boundingVolume": _scene_model_region(model),
                "geometricError": max(20.0, model_lod_errors(model.get("_bbox") or {})["lod0"]),
                "refine": "REPLACE",
                "content": {
                    "uri": os.path.relpath(model_tileset, scene_dir).replace("\\", "/")
                },
            })

        if not model_children:
            continue

        scene_children.append({
            "boundingVolume": leaf.bounds.to_region(),
            "geometricError": cell_error,
            "refine": "ADD",
            "children": model_children,
        })

    if not scene_children:
        return None

    scene_tileset = {
        "asset": {"version": "1.0"},
        "geometricError": scene_root_error,
        "root": {
            "boundingVolume": tree.bounds.to_region(),
            "geometricError": cell_error,
            "refine": "ADD",
            "children": scene_children,
        },
    }

    os.makedirs(scene_dir, exist_ok=True)
    scene_path = os.path.join(scene_dir, "tileset.json")
    with open(scene_path, "w", encoding="utf-8") as handle:
        json.dump(scene_tileset, handle, indent=2)
    return scene_path
