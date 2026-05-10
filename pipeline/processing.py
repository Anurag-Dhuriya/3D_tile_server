import json
import math
import os
import shutil
import subprocess
import time

from .citydb_adapter import (
    export_citydb_tiles,
    import_city_model_to_db,
    should_use_citydb_backend,
)
from .citygml import extract_citygml_buildings
from .mesh_backend import normalize_mesh, generate_lod_glb
from .settings import pipeline_settings_for_model
from .spatial_chunker import should_spatially_chunk, chunk_scene_to_glb_assets
from .tileset_builder import build_model_tileset, build_scene_tileset


MIN_FACE_LIMIT = 100
MIN_FACE_DROP_RATIO = 0.18
MIN_ERROR_STEP_METERS = 0.15
MAX_DYNAMIC_LEVELS = 6

_M_PER_DEG_LON_EQ = 111319.49079327357
_M_PER_DEG_LAT = 110574.0


def emit_stage(stage_hook, stage, detail=None):
    if stage_hook is None:
        return
    try:
        stage_hook(stage, detail)
    except Exception:
        pass


def resolve_source_file(paths, file_name):
    candidates = [
        os.path.join(paths["upload_dir"], file_name),
        os.path.join(paths["models_dir"], file_name),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def read_bbox_file(path):
    default_bbox = {"width": 20.0, "depth": 20.0, "height": 10.0}
    if not os.path.isfile(path):
        return default_bbox

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip().split(",")
        if len(raw) < 3:
            return default_bbox
        return {
            "width": float(raw[0]),
            "depth": float(raw[1]),
            "height": float(raw[2]),
        }
    except Exception:
        return default_bbox


def read_meta_file(path):
    default_meta = {
        "width": 20.0,
        "depth": 20.0,
        "height": 10.0,
        "faces": 0,
        "vertices": 0,
        "file_size_bytes": 0,
        "disconnected_parts": 1,
        "geometric_complexity": 0.0,
        "fine_detail_score": 0.0,
        "repeated_structure_score": 0.0,
        "slenderness": 1.0,
        "diagonal": 1.0,
        "has_texture": False,
    }
    if not os.path.isfile(path):
        return default_meta

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        merged = dict(default_meta)
        merged.update(payload)
        return merged
    except Exception:
        return default_meta


def glb_to_b3dm(tool_path, glb_path, b3dm_path):
    output_dir = os.path.dirname(b3dm_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    result = subprocess.run(
        [tool_path, "glbToB3dm", "-i", glb_path, "-o", b3dm_path, "-f"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"glbToB3dm failed for {glb_path}")
    if not os.path.isfile(b3dm_path):
        raise RuntimeError(f"b3dm not created: {b3dm_path}")


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def safe_log_scale(value, minimum, maximum):
    if maximum <= minimum:
        return 0.0
    value = clamp(value, minimum, maximum)
    return (math.log10(value) - math.log10(minimum)) / (
        math.log10(maximum) - math.log10(minimum)
    )


def derive_model_scores(meta):
    width = float(meta.get("width", 20.0))
    depth = float(meta.get("depth", 20.0))
    height = float(meta.get("height", 10.0))
    faces = max(int(meta.get("faces", 0)), MIN_FACE_LIMIT)
    vertices = max(int(meta.get("vertices", 0)), MIN_FACE_LIMIT)
    diagonal = max(
        float(meta.get("diagonal", math.sqrt(width * width + depth * depth + height * height))),
        1.0,
    )
    file_size_bytes = max(int(meta.get("file_size_bytes", 0)), 1)
    disconnected_parts = max(int(meta.get("disconnected_parts", 1)), 1)
    geometric_complexity = clamp(float(meta.get("geometric_complexity", 0.0)), 0.0, 1.0)
    fine_detail_score = clamp(float(meta.get("fine_detail_score", 0.0)), 0.0, 1.0)
    repeated_structure_score = clamp(float(meta.get("repeated_structure_score", 0.0)), 0.0, 1.0)
    slenderness = max(float(meta.get("slenderness", 1.0)), 1.0)

    face_score = safe_log_scale(faces, 100, 500000)
    vertex_score = safe_log_scale(vertices, 100, 500000)
    size_score = safe_log_scale(diagonal, 1.0, 300.0)
    file_score = safe_log_scale(file_size_bytes, 50000, 200000000)
    component_score = clamp((disconnected_parts - 1) / 12.0, 0.0, 1.0)
    aspect_score = clamp((slenderness - 1.0) / 10.0, 0.0, 1.0)

    detail_score = clamp(
        face_score * 0.24
        + vertex_score * 0.12
        + size_score * 0.14
        + file_score * 0.10
        + geometric_complexity * 0.16
        + fine_detail_score * 0.14
        + component_score * 0.06
        + repeated_structure_score * 0.02
        + aspect_score * 0.02,
        0.0,
        1.0,
    )

    preservation_bias = clamp(
        geometric_complexity * 0.34
        + fine_detail_score * 0.30
        + component_score * 0.16
        + repeated_structure_score * 0.12
        + aspect_score * 0.08,
        0.0,
        1.0,
    )

    return {
        "faces": faces,
        "vertices": vertices,
        "diagonal": diagonal,
        "file_size_bytes": file_size_bytes,
        "disconnected_parts": disconnected_parts,
        "geometric_complexity": geometric_complexity,
        "fine_detail_score": fine_detail_score,
        "repeated_structure_score": repeated_structure_score,
        "slenderness": slenderness,
        "face_score": round(face_score, 4),
        "vertex_score": round(vertex_score, 4),
        "size_score": round(size_score, 4),
        "file_score": round(file_score, 4),
        "component_score": round(component_score, 4),
        "aspect_score": round(aspect_score, 4),
        "detail_score": round(detail_score, 4),
        "preservation_bias": round(preservation_bias, 4),
    }


def choose_level_count(scores):
    faces = scores["faces"]
    detail_score = scores["detail_score"]

    if faces < 800 and detail_score < 0.25:
        return 3
    if detail_score < 0.45:
        return 4
    if detail_score < 0.72:
        return 5
    return 6


def build_error_fractions(level_count, preservation_bias):
    if level_count <= 1:
        return [0.0]

    max_fraction = clamp(0.72 - preservation_bias * 0.22, 0.35, 0.72)
    fractions = []
    steps = level_count - 1

    for index in range(steps):
        t = index / max(steps, 1)
        eased = 1.0 - (t ** 1.35)
        fractions.append(round(max_fraction * eased, 4))

    fractions.append(0.0)
    return fractions


def estimate_ratio_from_error_fraction(error_fraction, preservation_bias=0.5):
    exponent = 3.0 + (1.0 - preservation_bias) * 1.6
    min_ratio = clamp(0.02 + preservation_bias * 0.06, 0.02, 0.08)
    return max(min_ratio, min(1.0, (1.0 - error_fraction) ** exponent))


def plan_dynamic_lods(meta):
    scores = derive_model_scores(meta)
    original_faces = scores["faces"]
    diagonal = scores["diagonal"]
    level_count = choose_level_count(scores)
    error_fractions = build_error_fractions(level_count, scores["preservation_bias"])

    candidates = []
    previous_target_faces = None
    previous_error = None

    for error_fraction in error_fractions[:MAX_DYNAMIC_LEVELS]:
        ratio = (
            1.0
            if error_fraction == 0.0
            else estimate_ratio_from_error_fraction(error_fraction, scores["preservation_bias"])
        )

        target_faces = max(MIN_FACE_LIMIT, int(round(original_faces * ratio)))
        estimated_error = round(diagonal * error_fraction, 3)

        if previous_target_faces is not None:
            face_drop = abs(previous_target_faces - target_faces) / max(previous_target_faces, 1)
            error_step = abs((previous_error or 0.0) - estimated_error)
            if face_drop < MIN_FACE_DROP_RATIO and error_step < MIN_ERROR_STEP_METERS:
                continue

        candidates.append({
            "ratio": ratio,
            "target_faces": target_faces,
            "estimated_error": estimated_error,
        })

        previous_target_faces = target_faces
        previous_error = estimated_error

    if not candidates or candidates[-1]["ratio"] < 0.999:
        candidates.append({
            "ratio": 1.0,
            "target_faces": original_faces,
            "estimated_error": 0.0,
        })

    deduped = []
    seen_faces = set()

    for candidate in candidates:
        key = candidate["target_faces"]
        if key in seen_faces:
            continue
        seen_faces.add(key)
        deduped.append(candidate)

    lod_plan = []
    for index, candidate in enumerate(deduped):
        lod_plan.append({
            "name": f"lod{index}",
            "ratio": round(candidate["ratio"], 4),
            "target_faces": candidate["target_faces"],
            "geometric_error": candidate["estimated_error"],
        })

    return {
        "levels": lod_plan,
        "original_faces": original_faces,
        "model_diagonal": round(diagonal, 3),
        "detail_score": scores["detail_score"],
        "preservation_bias": scores["preservation_bias"],
        "level_count": len(lod_plan),
        "analysis": scores,
    }


def generate_lod_glbs(source_glb, lod_dir, asset_name, tools, lod_plan, has_texture=False):
    shutil.rmtree(lod_dir, ignore_errors=True)
    os.makedirs(lod_dir, exist_ok=True)

    lod_paths = {}

    for level in lod_plan:
        level_name = level["name"]
        ratio = float(level["ratio"])

        level_dir = os.path.join(lod_dir, level_name)
        os.makedirs(level_dir, exist_ok=True)

        output_glb = os.path.join(level_dir, f"{asset_name}.glb")

        print(
            f"[LOD] {level_name}: ratio={ratio:.4f}, "
            f"target_faces={level['target_faces']}, "
            f"estimated_error={level['geometric_error']:.3f}m"
        )

        generate_lod_glb(
            input_glb=source_glb,
            output_glb=output_glb,
            ratio=ratio,
            target_faces=level["target_faces"],
            has_texture=has_texture,
            tools=tools,
        )

        lod_paths[level_name] = output_glb

    return lod_paths


def process_mesh_asset(asset_name, source_path, unit, paths, tools, model_name, output_dir, stage_hook=None):
    safe_asset_name = f"{model_name}__{asset_name}"
    normalized_glb = os.path.join(paths["models_dir"], f"{safe_asset_name}.glb")
    glb_stem = os.path.join(paths["models_dir"], safe_asset_name)
    bbox_path = glb_stem + "_bbox.txt"
    meta_path = glb_stem + "_meta.json"

    print(f"[Pipeline] Normalizing {asset_name} from {os.path.basename(source_path)}")
    emit_stage(stage_hook, "normalizing_mesh", asset_name)

    normalize_mesh(
        source_path=source_path,
        output_glb=normalized_glb,
        unit=unit,
    )

    bbox = read_bbox_file(bbox_path)
    meta = read_meta_file(meta_path)

    if os.path.isfile(bbox_path):
        os.remove(bbox_path)
    if os.path.isfile(meta_path):
        os.remove(meta_path)

    emit_stage(stage_hook, "planning_lods", asset_name)
    dynamic_plan = plan_dynamic_lods(meta)

    print(
        f"[LOD] Planned {len(dynamic_plan['levels'])} levels for {asset_name} "
        f"(faces={dynamic_plan['original_faces']}, "
        f"diagonal={dynamic_plan['model_diagonal']}m, "
        f"detail_score={dynamic_plan['detail_score']:.3f}, "
        f"preservation_bias={dynamic_plan['preservation_bias']:.3f})"
    )

    emit_stage(stage_hook, "generating_lods", asset_name)
    lod_glbs = generate_lod_glbs(
        source_glb=normalized_glb,
        lod_dir=os.path.join(paths["lod_dir"], model_name, asset_name),
        asset_name=asset_name,
        tools=tools,
        lod_plan=dynamic_plan["levels"],
        has_texture=meta.get("has_texture", False),
    )

    b3dm_map = {}
    emit_stage(stage_hook, "converting_b3dm", asset_name)

    for level in dynamic_plan["levels"]:
        level_name = level["name"]
        glb_path = lod_glbs[level_name]
        b3dm_path = os.path.join(
            output_dir,
            "buildings",
            asset_name,
            level_name,
            "content.b3dm",
        )

        print(f"[Pipeline] {model_name} {asset_name} {level_name}: GLB -> b3dm")
        glb_to_b3dm(tools["tiles_tools_path"], glb_path, b3dm_path)
        b3dm_map[level_name] = b3dm_path

    return {
        "bbox": bbox,
        "lod_plan": dynamic_plan["levels"],
        "analysis": dynamic_plan["analysis"],
        "b3dm_map": b3dm_map,
    }


def _citygml_lod_payload():
    return [
        {
            "name": "multi-building",
            "ratio": 1.0,
            "target_faces": 0,
            "geometric_error": 0.0,
        }
    ]


def extract_and_process_citygml_buildings(source_path, citygml_dir, name, paths, tools, output_dir, stage_hook=None):
    emit_stage(stage_hook, "parsing_citygml", os.path.basename(source_path))
    manifest = extract_citygml_buildings(source_path, citygml_dir)

    building_entries = []

    for building in manifest["buildings"]:
        asset_name = building["name"]
        asset_source = os.path.join(citygml_dir, building["file"])

        try:
            emit_stage(stage_hook, "processing_citygml_building", asset_name)
            asset_result = process_mesh_asset(
                asset_name=asset_name,
                source_path=asset_source,
                unit="m",
                paths=paths,
                tools=tools,
                model_name=name,
                output_dir=output_dir,
                stage_hook=stage_hook,
            )

            if not asset_result.get("b3dm_map"):
                print(f"[CityGML] Skipping {asset_name}: no b3dm output")
                continue

            building_entries.append({
                "name": asset_name,
                "bbox": asset_result["bbox"],
                "lod_plan": asset_result["lod_plan"],
                "b3dm_map": asset_result["b3dm_map"],
                "offset_x": building["offset_x"],
                "offset_y": building["offset_y"],
                "offset_z": building["offset_z"],
                "analysis": asset_result["analysis"],
            })

        except Exception as exc:
            print(f"[CityGML] Skipping building {asset_name}: {exc}")
            continue

    if not building_entries:
        raise RuntimeError(f"CityGML processing produced no valid buildings for source '{source_path}'")

    return manifest, building_entries


def _approx_dataset_bbox_from_tiles(tile_models):
    min_lon = float("inf")
    max_lon = float("-inf")
    min_lat = float("inf")
    max_lat = float("-inf")
    min_height = float("inf")
    max_height = float("-inf")

    for model in tile_models:
        bbox = model.get("_bbox") or {}
        width = float(bbox.get("width", 1.0))
        depth = float(bbox.get("depth", 1.0))
        height = float(bbox.get("height", 1.0))
        lon = float(model["lon"])
        lat = float(model["lat"])
        base_height = float(model.get("height", 0.0))

        lon_delta = (width / 2.0) / (_M_PER_DEG_LON_EQ * max(1e-9, math.cos(math.radians(lat))))
        lat_delta = (depth / 2.0) / _M_PER_DEG_LAT

        min_lon = min(min_lon, lon - lon_delta)
        max_lon = max(max_lon, lon + lon_delta)
        min_lat = min(min_lat, lat - lat_delta)
        max_lat = max(max_lat, lat + lat_delta)
        min_height = min(min_height, base_height)
        max_height = max(max_height, base_height + height)

    center_lat = (min_lat + max_lat) / 2.0 if min_lat != float("inf") else 0.0

    width_m = abs(max_lon - min_lon) * _M_PER_DEG_LON_EQ * max(1e-9, math.cos(math.radians(center_lat)))
    depth_m = abs(max_lat - min_lat) * _M_PER_DEG_LAT
    height_m = max(max_height - min_height, 1.0)

    return {
        "width": max(width_m, 1.0),
        "depth": max(depth_m, 1.0),
        "height": max(height_m, 1.0),
    }


def process_citydb_backed_citygml(model, source_path, paths, tools, output_dir, stage_hook=None):
    name = model["name"]
    settings = pipeline_settings_for_model(model)

    citydb_export_dir = os.path.join(paths["models_dir"], f"{name}_citydb_export")
    tile_tiles_root = os.path.join(output_dir, "_citydb_tiles")

    shutil.rmtree(citydb_export_dir, ignore_errors=True)
    shutil.rmtree(tile_tiles_root, ignore_errors=True)

    import_start = time.perf_counter()
    import_city_model_to_db(source_path, name, settings, stage_hook=stage_hook)
    import_sec = time.perf_counter() - import_start

    export_start = time.perf_counter()
    exported_tiles = export_citydb_tiles(name, citydb_export_dir, settings, stage_hook=stage_hook)
    export_sec = time.perf_counter() - export_start

    ready_tile_models = []
    total_buildings = 0
    tile_process_start = time.perf_counter()

    for tile_index, tile_source in enumerate(exported_tiles):
        tile_name = f"tile_{tile_index:04d}"
        tile_citygml_dir = os.path.join(paths["models_dir"], f"{name}_{tile_name}_citygml")
        tile_output_dir = os.path.join(tile_tiles_root, tile_name)

        shutil.rmtree(tile_citygml_dir, ignore_errors=True)
        shutil.rmtree(tile_output_dir, ignore_errors=True)

        emit_stage(stage_hook, "processing_citydb_tile", tile_name)

        manifest, building_entries = extract_and_process_citygml_buildings(
            source_path=tile_source,
            citygml_dir=tile_citygml_dir,
            name=f"{name}_{tile_name}",
            paths=paths,
            tools=tools,
            output_dir=tile_output_dir,
            stage_hook=stage_hook,
        )

        lod_plan_payload = _citygml_lod_payload()

        tileset_path = build_model_tileset(
            output_folder=tile_output_dir,
            bbox=manifest["bbox"],
            lon=manifest["origin_lon"],
            lat=manifest["origin_lat"],
            height=manifest["origin_height"],
            chunks=building_entries,
        )
        if not tileset_path:
            raise RuntimeError(f"Failed to build tileset for exported DB tile '{tile_name}'")

        analysis_payload = {
            "source_type": "citydb_citygml_tile",
            "building_count": len(building_entries),
            "origin_lon": manifest["origin_lon"],
            "origin_lat": manifest["origin_lat"],
        }

        write_bbox_json(
            tile_output_dir,
            manifest["bbox"],
            lod_plan=lod_plan_payload,
            analysis=analysis_payload,
        )

        ready_tile_models.append({
            "name": tile_name,
            "lon": manifest["origin_lon"],
            "lat": manifest["origin_lat"],
            "height": manifest["origin_height"],
            "_bbox": manifest["bbox"],
            "_lod_plan": lod_plan_payload,
        })

        total_buildings += len(building_entries)

    tile_process_sec = time.perf_counter() - tile_process_start

    if not ready_tile_models:
        raise RuntimeError("3DCityDB export produced tiles, but none were successfully processed")

    emit_stage(stage_hook, "building_tileset", f"{len(ready_tile_models)} 3DCityDB tiles")
    dataset_tileset_start = time.perf_counter()

    tileset_path = build_scene_tileset(
        scene_dir=output_dir,
        tiles_dir=tile_tiles_root,
        ready_models=ready_tile_models,
    )
    if not tileset_path:
        raise RuntimeError("Failed to build aggregated tileset for 3DCityDB export tiles")

    tileset_sec = time.perf_counter() - dataset_tileset_start
    bbox = _approx_dataset_bbox_from_tiles(ready_tile_models)

    analysis_payload = {
        "source_type": "3dcitydb",
        "export_tile_count": len(ready_tile_models),
        "building_count": total_buildings,
        "tile_dimension_m": float(settings.get("citydb_tile_dimension_m", 500.0)),
    }

    lod_plan_payload = [
        {
            "name": "citydb-tiles",
            "ratio": 1.0,
            "target_faces": 0,
            "geometric_error": max(bbox["width"], bbox["depth"]) * 0.5,
        }
    ]

    return {
        "bbox": bbox,
        "tileset_path": tileset_path,
        "analysis": analysis_payload,
        "lod_plan": lod_plan_payload,
        "timings": {
            "normalize_sec": round(import_sec + export_sec, 2),
            "lod_plan_sec": 0.0,
            "lod_generation_sec": round(tile_process_sec, 2),
            "b3dm_conversion_sec": 0.0,
            "tileset_build_sec": round(tileset_sec, 2),
            "total_pipeline_sec": round(import_sec + export_sec + tile_process_sec + tileset_sec, 2),
        },
    }


def process_spatially_chunked_mesh(model, source_path, paths, tools, output_dir, stage_hook=None):
    model_name = model["name"]
    unit = model.get("unit", "m")

    lon = float(model.get("lon", 0.0))
    lat = float(model.get("lat", 0.0))
    height = float(model.get("height", 0.0))

    settings = pipeline_settings_for_model(model)
    chunk_mode = settings["spatial_chunking"]
    chunk_size_m = float(settings["chunk_size_m"])
    max_chunks = int(settings["max_chunks"])

    normalized_glb = os.path.join(paths["models_dir"], f"{model_name}__source.glb")
    glb_stem = os.path.join(paths["models_dir"], f"{model_name}__source")
    bbox_path = glb_stem + "_bbox.txt"
    meta_path = glb_stem + "_meta.json"

    print(f"[Pipeline] Normalizing full source for chunking: {model_name}")
    emit_stage(stage_hook, "normalizing_source", model_name)

    normalize_mesh(
        source_path=source_path,
        output_glb=normalized_glb,
        unit=unit,
    )

    bbox = read_bbox_file(bbox_path)
    meta = read_meta_file(meta_path)

    if not should_spatially_chunk(meta, bbox, chunk_mode):
        print("[Chunking] Model is small enough; using normal single-model pipeline")
        for path in (bbox_path, meta_path, normalized_glb):
            if os.path.isfile(path):
                os.remove(path)
        return None

    chunk_source_dir = os.path.join(paths["models_dir"], f"{model_name}_chunks")
    shutil.rmtree(chunk_source_dir, ignore_errors=True)

    emit_stage(stage_hook, "partitioning_scene", f"chunk_size={chunk_size_m}")
    chunk_records = chunk_scene_to_glb_assets(
        source_glb=normalized_glb,
        output_dir=chunk_source_dir,
        chunk_size_m=chunk_size_m,
        max_chunks=max_chunks,
    )

    for path in (bbox_path, meta_path, normalized_glb):
        if os.path.isfile(path):
            os.remove(path)

    building_entries = []

    for record in chunk_records:
        asset_name = record["name"]

        print(
            f"[Chunking] Processing {asset_name} "
            f"faces={record['faces']} "
            f"offset=({record['offset_x']}, {record['offset_y']}, {record['offset_z']})"
        )

        try:
            emit_stage(stage_hook, "processing_chunk", asset_name)
            asset_result = process_mesh_asset(
                asset_name=asset_name,
                source_path=record["file_path"],
                unit="m",
                paths=paths,
                tools=tools,
                model_name=model_name,
                output_dir=output_dir,
                stage_hook=stage_hook,
            )

            if asset_result is None:
                print(f"[Chunking] Skipping {asset_name}: no asset result")
                continue

            if not asset_result.get("b3dm_map"):
                print(f"[Chunking] Skipping {asset_name}: no b3dm output")
                continue

            building_entries.append({
                "name": asset_name,
                "bbox": asset_result["bbox"],
                "lod_plan": asset_result["lod_plan"],
                "b3dm_map": asset_result["b3dm_map"],
                "offset_x": record["offset_x"],
                "offset_y": record["offset_y"],
                "offset_z": record["offset_z"],
                "analysis": asset_result["analysis"],
            })

        except Exception as exc:
            print(f"[Chunking] Skipping {asset_name}: {exc}")
            continue

    if not building_entries:
        raise RuntimeError("Spatial chunking finished, but no valid chunks were processed")

    print(f"[Chunking] Valid processed chunks: {len(building_entries)}")
    emit_stage(stage_hook, "building_tileset", f"{len(building_entries)} chunks")

    tileset_path = build_model_tileset(
        output_folder=output_dir,
        bbox=bbox,
        lon=lon,
        lat=lat,
        height=height,
        chunks=building_entries,
    )

    analysis_payload = {
        "source_type": "spatial_chunks",
        "chunk_count": len(building_entries),
        "chunk_size_m": chunk_size_m,
        "faces": meta.get("faces", 0),
        "vertices": meta.get("vertices", 0),
        "diagonal": meta.get("diagonal", 0),
        "has_texture": meta.get("has_texture", False),
    }

    lod_plan_payload = [
        {
            "name": "spatial-chunks",
            "ratio": 1.0,
            "target_faces": meta.get("faces", 0),
            "geometric_error": max(float(bbox["width"]), float(bbox["depth"])) * 0.5,
        }
    ]

    return {
        "bbox": bbox,
        "tileset_path": tileset_path,
        "analysis": analysis_payload,
        "lod_plan": lod_plan_payload,
    }


def build_model_artifacts(model, paths, tools, stage_hook=None):
    overall_start = time.perf_counter()

    name = model["name"]
    file_name = model["file"]
    unit = model.get("unit", "m")
    lon = float(model.get("lon", 0.0))
    lat = float(model.get("lat", 0.0))
    height = float(model.get("height", 0.0))

    source_path = resolve_source_file(paths, file_name)
    if not source_path:
        raise FileNotFoundError(f"Source file not found: {file_name}")

    ext = os.path.splitext(source_path)[1].lower()
    if ext not in {".obj", ".glb", ".gltf", ".gml", ".xml", ".cityjson", ".json"}:
        raise ValueError(f"Unsupported format: {ext}")

    output_dir = os.path.join(paths["tiles_dir"], name)
    citygml_dir = os.path.join(paths["models_dir"], f"{name}_citygml")

    shutil.rmtree(output_dir, ignore_errors=True)
    shutil.rmtree(citygml_dir, ignore_errors=True)

    settings = pipeline_settings_for_model(model)

    if ext in {".gml", ".xml"} and should_use_citydb_backend(model, source_path=source_path, settings=settings):
        return process_citydb_backed_citygml(
            model=model,
            source_path=source_path,
            paths=paths,
            tools=tools,
            output_dir=output_dir,
            stage_hook=stage_hook,
        )

    if ext in {".gml", ".xml"}:
        parse_start = time.perf_counter()
        print(f"[Pipeline] Parsing CityGML for {name} from {os.path.basename(source_path)}")

        manifest, building_entries = extract_and_process_citygml_buildings(
            source_path=source_path,
            citygml_dir=citygml_dir,
            name=name,
            paths=paths,
            tools=tools,
            output_dir=output_dir,
            stage_hook=stage_hook,
        )
        parse_sec = time.perf_counter() - parse_start

        emit_stage(stage_hook, "building_tileset", f"{len(building_entries)} CityGML buildings")
        tileset_start = time.perf_counter()
        tileset_path = build_model_tileset(
            output_folder=output_dir,
            bbox=manifest["bbox"],
            lon=manifest["origin_lon"],
            lat=manifest["origin_lat"],
            height=manifest["origin_height"],
            chunks=building_entries,
        )
        tileset_sec = time.perf_counter() - tileset_start

        bbox = manifest["bbox"]
        analysis_payload = {
            "source_type": "citygml",
            "building_count": len(building_entries),
            "origin_lon": manifest["origin_lon"],
            "origin_lat": manifest["origin_lat"],
        }
        lod_plan_payload = _citygml_lod_payload()

        return {
            "bbox": bbox,
            "tileset_path": tileset_path,
            "analysis": analysis_payload,
            "lod_plan": lod_plan_payload,
            "timings": {
                "normalize_sec": round(parse_sec, 2),
                "lod_plan_sec": 0.0,
                "lod_generation_sec": 0.0,
                "b3dm_conversion_sec": 0.0,
                "tileset_build_sec": round(tileset_sec, 2),
                "total_pipeline_sec": round(time.perf_counter() - overall_start, 2),
            },
        }

    chunked_result = process_spatially_chunked_mesh(
        model=model,
        source_path=source_path,
        paths=paths,
        tools=tools,
        output_dir=output_dir,
        stage_hook=stage_hook,
    )

    if chunked_result is not None:
        process_sec = time.perf_counter() - overall_start

        tileset_path = chunked_result["tileset_path"]
        bbox = chunked_result["bbox"]
        analysis_payload = chunked_result["analysis"]
        lod_plan_payload = chunked_result["lod_plan"]

        return {
            "bbox": bbox,
            "tileset_path": tileset_path,
            "analysis": analysis_payload,
            "lod_plan": lod_plan_payload,
            "timings": {
                "normalize_sec": round(process_sec, 2),
                "lod_plan_sec": 0.0,
                "lod_generation_sec": 0.0,
                "b3dm_conversion_sec": 0.0,
                "tileset_build_sec": 0.0,
                "total_pipeline_sec": round(time.perf_counter() - overall_start, 2),
            },
        }

    mesh_start = time.perf_counter()
    asset_result = process_mesh_asset(
        asset_name=name,
        source_path=source_path,
        unit=unit,
        paths=paths,
        tools=tools,
        model_name=name,
        output_dir=output_dir,
        stage_hook=stage_hook,
    )

    process_sec = time.perf_counter() - mesh_start

    emit_stage(stage_hook, "building_tileset", name)
    tileset_start = time.perf_counter()
    tileset_path = build_model_tileset(
        output_folder=output_dir,
        bbox=asset_result["bbox"],
        lon=lon,
        lat=lat,
        height=height,
        b3dm_map=asset_result["b3dm_map"],
        lod_plan=asset_result["lod_plan"],
    )
    tileset_sec = time.perf_counter() - tileset_start

    bbox = asset_result["bbox"]
    analysis_payload = asset_result["analysis"]
    lod_plan_payload = asset_result["lod_plan"]

    return {
        "bbox": bbox,
        "tileset_path": tileset_path,
        "analysis": analysis_payload,
        "lod_plan": lod_plan_payload,
        "timings": {
            "normalize_sec": round(process_sec, 2),
            "lod_plan_sec": 0.0,
            "lod_generation_sec": 0.0,
            "b3dm_conversion_sec": 0.0,
            "tileset_build_sec": round(tileset_sec, 2),
            "total_pipeline_sec": round(time.perf_counter() - overall_start, 2),
        },
    }


def rebuild_scene(config, paths):
    ready_models = []

    for model in config.get("models", []):
        if model.get("status") != "ready":
            continue

        model_tileset = os.path.join(paths["tiles_dir"], model["name"], "tileset.json")
        if not os.path.isfile(model_tileset):
            continue

        meta_path = os.path.join(paths["tiles_dir"], model["name"], "bbox.json")

        bbox = {"width": 20.0, "depth": 20.0, "height": 10.0}
        lod_plan = []

        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

            if isinstance(payload, dict) and "bbox" in payload:
                bbox = payload.get("bbox", bbox)
                lod_plan = payload.get("lod_plan", [])
            else:
                bbox = payload

        ready_models.append({**model, "_bbox": bbox, "_lod_plan": lod_plan})

    return build_scene_tileset(
        scene_dir=paths["scene_dir"],
        tiles_dir=paths["tiles_dir"],
        ready_models=ready_models,
    )


def write_bbox_json(output_dir, bbox, lod_plan=None, analysis=None):
    os.makedirs(output_dir, exist_ok=True)

    bbox_path = os.path.join(output_dir, "bbox.json")
    payload = {"bbox": bbox}

    if lod_plan is not None:
        payload["lod_plan"] = lod_plan

    if analysis is not None:
        payload["analysis"] = analysis

    with open(bbox_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    return bbox_path
