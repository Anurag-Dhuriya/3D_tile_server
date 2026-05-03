import json
import math
import os

from .geo import east_north_up_transform, meters_to_lat_delta, meters_to_lon_delta
from .quadtree import build_quadtree


def fallback_lod_plan(bbox):
    size = max(
        float(bbox.get("width", 0.0)),
        float(bbox.get("depth", 0.0)),
        float(bbox.get("height", 0.0)),
        1.0,
    )
    return [
        {"name": "lod0", "ratio": 0.15, "target_faces": 0, "geometric_error": max(20.0, size * 4.0)},
        {"name": "lod1", "ratio": 0.45, "target_faces": 0, "geometric_error": max(5.0, size * 1.5)},
        {"name": "lod2", "ratio": 1.0, "target_faces": 0, "geometric_error": 0.0},
    ]


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


def local_translation_transform(x, y, z):
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        float(x), float(y), float(z), 1.0,
    ]


def build_model_tileset(output_folder, bbox, lon, lat, height, b3dm_map=None, lod_plan=None, chunks=None):
    if not lod_plan:
        lod_plan = fallback_lod_plan(bbox)

    def make_node(level_plan, content_map, local_bbox, level_index):
        level = level_plan[level_index]
        level_name = level["name"]
        content_path = content_map.get(level_name)
        if not content_path:
            return None

        node = {
            "boundingVolume": make_box_bounding_volume(local_bbox),
            "geometricError": float(level.get("geometric_error", 0.0)),
            "refine": "REPLACE",
            "content": {
                "uri": os.path.relpath(content_path, output_folder).replace("\\", "/")
            },
        }

        next_index = level_index + 1
        if next_index < len(level_plan):
            child = make_node(level_plan, content_map, local_bbox, next_index)
            if child:
                node["children"] = [child]

        return node

    if chunks:
        children = []
        root_error = 1.0

        for chunk in chunks:
            chunk_plan = chunk.get("lod_plan") or fallback_lod_plan(chunk.get("bbox") or {})
            child = make_node(
                chunk_plan,
                chunk.get("b3dm_map") or {},
                chunk.get("bbox") or bbox,
                0,
            )
            if not child:
                continue

            child["transform"] = local_translation_transform(
                chunk.get("offset_x", 0.0),
                chunk.get("offset_y", 0.0),
                chunk.get("offset_z", 0.0),
            )
            children.append(child)
            root_error = max(root_error, float(chunk_plan[0].get("geometric_error", 1.0)))

        if not children:
            return None

        # FIX: Empty parent wrapper needs a massive error to force refinement to chunks
        root = {
            "boundingVolume": make_box_bounding_volume(bbox),
            "geometricError": 10000000.0,
            "refine": "ADD",
            "transform": east_north_up_transform(lon, lat, height),
            "children": children,
        }
        # FIX: Tileset wrapper needs massive error
        tileset = {
            "asset": {"version": "1.0"},
            "geometricError": 10000000.0,
            "root": root,
        }
    else:
        root = make_node(lod_plan, b3dm_map or {}, bbox, 0)
        if root is None:
            return None

        root["transform"] = east_north_up_transform(lon, lat, height)

        # The root node here HAS content (lod0.b3dm), so it keeps its calculated error.
        # FIX: But the tileset wrapper needs to be huge so the viewer loads the root.
        tileset = {
            "asset": {"version": "1.0"},
            "geometricError": 10000000.0,
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

    scene_children = []
    for leaf in leaves:
        model_children = []
        for model in leaf.models:
            model_tileset = os.path.join(tiles_dir, model["name"], "tileset.json")
            if not os.path.isfile(model_tileset):
                continue

            lod_plan = model.get("_lod_plan") or fallback_lod_plan(model.get("_bbox") or {})
            root_model_error = float(lod_plan[0].get("geometric_error", 20.0))

            model_children.append({
                "boundingVolume": _scene_model_region(model),
                "geometricError": max(1.0, root_model_error),
                "refine": "REPLACE",
                "content": {
                    "uri": os.path.relpath(model_tileset, scene_dir).replace("\\", "/")
                },
            })

        if not model_children:
            continue
        
        # FIX: Empty quadtree cells must force refinement down to the actual models
        scene_children.append({
            "boundingVolume": leaf.bounds.to_region(),
            "geometricError": 10000000.0,
            "refine": "ADD",
            "children": model_children,
        })

    if not scene_children:
        return None

    # FIX: Force scene rendering on both the tileset object and the empty root node
    scene_tileset = {
        "asset": {"version": "1.0"},
        "geometricError": 10000000.0,
        "root": {
            "boundingVolume": tree.bounds.to_region(),
            "geometricError": 10000000.0,
            "refine": "ADD",
            "children": scene_children,
        },
    }

    os.makedirs(scene_dir, exist_ok=True)
    scene_path = os.path.join(scene_dir, "tileset.json")
    with open(scene_path, "w", encoding="utf-8") as handle:
        json.dump(scene_tileset, handle, indent=2)
    return scene_path