import json
import math
import os
import shutil
import subprocess
import time

from .citygml import extract_citygml_buildings
from .tileset_builder import build_model_tileset, build_scene_tileset


MIN_FACE_LIMIT = 100
MIN_FACE_DROP_RATIO = 0.18
MIN_ERROR_STEP_METERS = 0.15
MAX_DYNAMIC_LEVELS = 6


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


def run_blender_step(blender_path, script_path, mode, input_path, output_path, extra_arg):
    command = [
        blender_path,
        "--background",
        "--python",
        script_path,
        "--",
        mode,
        input_path,
        output_path,
        str(extra_arg),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
    )

    for line in result.stdout.splitlines():
        if "[Blender]" in line:
            print(line)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Blender {mode} step failed")

    if not os.path.isfile(output_path):
        raise RuntimeError(f"Expected output not found: {output_path}")


def glb_to_b3dm(tool_path, glb_path, b3dm_path):
    os.makedirs(os.path.dirname(b3dm_path), exist_ok=True)
    result = subprocess.run(
        [tool_path, "glbToB3dm", "-i", glb_path, "-o", b3dm_path, "-f"],
        capture_output=True,
        text=True,
        timeout=180,
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
    return (math.log10(value) - math.log10(minimum)) / (math.log10(maximum) - math.log10(minimum))


def derive_model_scores(meta):
    width = float(meta.get("width", 20.0))
    depth = float(meta.get("depth", 20.0))
    height = float(meta.get("height", 10.0))
    faces = max(int(meta.get("faces", 0)), MIN_FACE_LIMIT)
    vertices = max(int(meta.get("vertices", 0)), MIN_FACE_LIMIT)
    diagonal = max(float(meta.get("diagonal", math.sqrt(width * width + depth * depth + height * height))), 1.0)
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
        t = index / max(steps - 1, 1)
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
    for candidate in reversed(candidates):
        key = candidate["target_faces"]
        if key in seen_faces:
            continue
        seen_faces.add(key)
        deduped.append(candidate)
    deduped.reverse()

    lod_plan = []
    for index, candidate in enumerate(deduped):
        lod_plan.append({
            "name": f"lod{index}",
            "ratio": round(candidate["ratio"], 4),
            "target_faces": candidate["target_faces"],
            "geometric_error": candidate["estimated_error"],
        })

    if len(lod_plan) == 1:
        lod_plan[0]["name"] = "lod0"

    return {
        "levels": lod_plan,
        "original_faces": original_faces,
        "model_diagonal": round(diagonal, 3),
        "detail_score": scores["detail_score"],
        "preservation_bias": scores["preservation_bias"],
        "level_count": len(lod_plan),
        "analysis": scores,
    }


def generate_lod_glbs(source_glb, lod_dir, asset_name, tools, lod_plan):
    shutil.rmtree(lod_dir, ignore_errors=True)
    os.makedirs(lod_dir, exist_ok=True)

    lod_paths = {}
    for level in lod_plan:
        level_name = level["name"]
        ratio = float(level["ratio"])

        level_dir = os.path.join(lod_dir, level_name)
        os.makedirs(level_dir, exist_ok=True)
        output_glb = os.path.join(level_dir, f"{asset_name}.glb")

        if ratio >= 0.999:
            shutil.copy2(source_glb, output_glb)
            print(f"[LOD] {level_name}: copied full-detail GLB")
        else:
            print(
                f"[LOD] {level_name}: ratio={ratio:.4f}, "
                f"target_faces={level['target_faces']}, "
                f"estimated_error={level['geometric_error']:.3f}m"
            )
            run_blender_step(
                blender_path=tools["blender_path"],
                script_path=tools["blender_script"],
                mode="lod",
                input_path=source_glb,
                output_path=output_glb,
                extra_arg=ratio,
            )

        lod_paths[level_name] = output_glb

    return lod_paths


def process_mesh_asset(asset_name, source_path, unit, paths, tools, model_name, output_dir):
    safe_asset_name = f"{model_name}__{asset_name}"
    normalized_glb = os.path.join(paths["models_dir"], f"{safe_asset_name}.glb")
    bbox_path = normalized_glb.replace(".glb", "_bbox.txt")
    meta_path = normalized_glb.replace(".glb", "_meta.json")

    print(f"[Pipeline] Normalizing {asset_name} from {os.path.basename(source_path)}")
    run_blender_step(
        blender_path=tools["blender_path"],
        script_path=tools["blender_script"],
        mode="normalize",
        input_path=source_path,
        output_path=normalized_glb,
        extra_arg=unit,
    )

    bbox = read_bbox_file(bbox_path)
    meta = read_meta_file(meta_path)
    if os.path.isfile(bbox_path):
        os.remove(bbox_path)
    if os.path.isfile(meta_path):
        os.remove(meta_path)

    dynamic_plan = plan_dynamic_lods(meta)
    print(
        f"[LOD] Planned {len(dynamic_plan['levels'])} levels for {asset_name} "
        f"(faces={dynamic_plan['original_faces']}, diagonal={dynamic_plan['model_diagonal']}m, "
        f"detail_score={dynamic_plan['detail_score']:.3f}, "
        f"preservation_bias={dynamic_plan['preservation_bias']:.3f})"
    )

    lod_glbs = generate_lod_glbs(
        source_glb=normalized_glb,
        lod_dir=os.path.join(paths["lod_dir"], model_name, asset_name),
        asset_name=asset_name,
        tools=tools,
        lod_plan=dynamic_plan["levels"],
    )

    b3dm_map = {}
    for level in dynamic_plan["levels"]:
        level_name = level["name"]
        glb_path = lod_glbs[level_name]
        b3dm_path = os.path.join(output_dir, "buildings", asset_name, level_name, "content.b3dm")
        print(f"[Pipeline] {model_name} {asset_name} {level_name}: GLB -> b3dm")
        glb_to_b3dm(tools["tiles_tools_path"], glb_path, b3dm_path)
        b3dm_map[level_name] = b3dm_path

    return {
        "bbox": bbox,
        "lod_plan": dynamic_plan["levels"],
        "analysis": dynamic_plan["analysis"],
        "b3dm_map": b3dm_map,
    }


def build_model_artifacts(model, paths, tools):
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
    if ext not in {".obj", ".glb", ".gltf", ".gml", ".xml"}:
        raise ValueError(f"Unsupported format: {ext}")

    output_dir = os.path.join(paths["tiles_dir"], name)
    citygml_dir = os.path.join(paths["models_dir"], f"{name}_citygml")

    shutil.rmtree(output_dir, ignore_errors=True)
    shutil.rmtree(citygml_dir, ignore_errors=True)

    if ext in {".gml", ".xml"}:
        parse_start = time.perf_counter()
        print(f"[Pipeline] Parsing CityGML for {name} from {os.path.basename(source_path)}")
        city_manifest = extract_citygml_buildings(source_path, citygml_dir)
        parse_sec = time.perf_counter() - parse_start

        building_entries = []
        building_start = time.perf_counter()
        for building in city_manifest["buildings"]:
            asset_name = building["name"]
            asset_source = os.path.join(citygml_dir, building["file"])
            asset_result = process_mesh_asset(
                asset_name=asset_name,
                source_path=asset_source,
                unit="m",
                paths=paths,
                tools=tools,
                model_name=name,
                output_dir=output_dir,
            )
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

        normalize_sec = round(parse_sec, 2)
        lod_plan_sec = 0.0
        lod_sec = time.perf_counter() - building_start
        b3dm_sec = 0.0

        tileset_start = time.perf_counter()
        tileset_path = build_model_tileset(
            output_folder=output_dir,
            bbox=city_manifest["bbox"],
            lon=city_manifest["origin_lon"],
            lat=city_manifest["origin_lat"],
            height=city_manifest["origin_height"],
            chunks=building_entries,
        )
        tileset_sec = time.perf_counter() - tileset_start

        bbox = city_manifest["bbox"]
        analysis_payload = {
            "source_type": "citygml",
            "building_count": len(building_entries),
            "origin_lon": city_manifest["origin_lon"],
            "origin_lat": city_manifest["origin_lat"],
        }
        lod_plan_payload = [
            {
                "name": "multi-building",
                "ratio": 1.0,
                "target_faces": 0,
                "geometric_error": 0.0,
            }
        ]

    else:
        single_start = time.perf_counter()
        asset_result = process_mesh_asset(
            asset_name=name,
            source_path=source_path,
            unit=unit,
            paths=paths,
            tools=tools,
            model_name=name,
            output_dir=output_dir,
        )
        process_sec = time.perf_counter() - single_start

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
        normalize_sec = round(process_sec, 2)
        lod_plan_sec = 0.0
        lod_sec = 0.0
        b3dm_sec = 0.0

    if not tileset_path:
        raise RuntimeError("Failed to build model tileset")

    total_sec = time.perf_counter() - overall_start

    timings = {
        "normalize_sec": round(normalize_sec, 2),
        "lod_plan_sec": round(lod_plan_sec, 2),
        "lod_generation_sec": round(lod_sec, 2),
        "b3dm_conversion_sec": round(b3dm_sec, 2),
        "tileset_build_sec": round(tileset_sec, 2),
        "total_pipeline_sec": round(total_sec, 2),
    }

    return {
        "bbox": bbox,
        "tileset_path": tileset_path,
        "timings": timings,
        "lod_plan": lod_plan_payload,
        "analysis": analysis_payload,
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
