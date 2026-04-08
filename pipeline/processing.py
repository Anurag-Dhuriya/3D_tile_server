import json
import math
import os
import shutil
import subprocess
import time

from .tileset_builder import build_model_tileset, build_scene_tileset


MIN_FACE_LIMIT = 100
MIN_FACE_DROP_RATIO = 0.18
MIN_ERROR_STEP_METERS = 0.15
MAX_DYNAMIC_LEVELS = 6

# Coarse-to-fine error fractions of model diagonal.
ERROR_FRACTIONS = [0.65, 0.40, 0.22, 0.10, 0.04, 0.0]


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
    }
    if not os.path.isfile(path):
        return default_meta

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return {
            "width": float(payload.get("width", 20.0)),
            "depth": float(payload.get("depth", 20.0)),
            "height": float(payload.get("height", 10.0)),
            "faces": int(payload.get("faces", 0)),
            "vertices": int(payload.get("vertices", 0)),
        }
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


def estimate_ratio_from_error_fraction(error_fraction):
    # Stronger decay than linear so coarse levels become meaningfully lighter.
    return max(0.02, min(1.0, (1.0 - error_fraction) ** 4))


def plan_dynamic_lods(meta):
    width = float(meta.get("width", 20.0))
    depth = float(meta.get("depth", 20.0))
    height = float(meta.get("height", 10.0))
    original_faces = max(int(meta.get("faces", 0)), MIN_FACE_LIMIT)

    diagonal = math.sqrt(width * width + depth * depth + height * height)
    if diagonal <= 0:
        diagonal = 1.0

    candidates = []
    previous_target_faces = None
    previous_error = None

    for error_fraction in ERROR_FRACTIONS[:MAX_DYNAMIC_LEVELS]:
        ratio = 1.0 if error_fraction == 0.0 else estimate_ratio_from_error_fraction(error_fraction)
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

    # Remove duplicate target face counts while keeping the finest version.
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
    }


def generate_lod_glbs(model_name, normalized_glb, paths, tools, lod_plan):
    lod_dir = os.path.join(paths["lod_dir"], model_name)
    shutil.rmtree(lod_dir, ignore_errors=True)
    os.makedirs(lod_dir, exist_ok=True)

    lod_paths = {}
    for level in lod_plan:
        level_name = level["name"]
        ratio = float(level["ratio"])

        level_dir = os.path.join(lod_dir, level_name)
        os.makedirs(level_dir, exist_ok=True)
        output_glb = os.path.join(level_dir, f"{model_name}.glb")

        if ratio >= 0.999:
            shutil.copy2(normalized_glb, output_glb)
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
                input_path=normalized_glb,
                output_path=output_glb,
                extra_arg=ratio,
            )

        lod_paths[level_name] = output_glb

    return lod_paths


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
    if ext not in {".obj", ".glb", ".gltf"}:
        raise ValueError(f"Unsupported format: {ext}")

    normalized_glb = os.path.join(paths["models_dir"], f"{name}.glb")
    bbox_path = normalized_glb.replace(".glb", "_bbox.txt")
    meta_path = normalized_glb.replace(".glb", "_meta.json")
    output_dir = os.path.join(paths["tiles_dir"], name)

    shutil.rmtree(output_dir, ignore_errors=True)

    normalize_start = time.perf_counter()
    print(f"[Pipeline] Normalizing {name} from {os.path.basename(source_path)}")
    run_blender_step(
        blender_path=tools["blender_path"],
        script_path=tools["blender_script"],
        mode="normalize",
        input_path=source_path,
        output_path=normalized_glb,
        extra_arg=unit,
    )
    normalize_sec = time.perf_counter() - normalize_start

    bbox = read_bbox_file(bbox_path)
    meta = read_meta_file(meta_path)
    if os.path.isfile(bbox_path):
        os.remove(bbox_path)
    if os.path.isfile(meta_path):
        os.remove(meta_path)

    lod_plan_start = time.perf_counter()
    dynamic_plan = plan_dynamic_lods(meta)
    lod_plan_sec = time.perf_counter() - lod_plan_start

    print(
        f"[LOD] Planned {len(dynamic_plan['levels'])} levels for {name} "
        f"(faces={dynamic_plan['original_faces']}, diagonal={dynamic_plan['model_diagonal']}m)"
    )
    for level in dynamic_plan["levels"]:
        print(
            f"[LOD] {level['name']}: ratio={level['ratio']:.4f}, "
            f"target_faces={level['target_faces']}, "
            f"geometric_error={level['geometric_error']:.3f}m"
        )

    lod_start = time.perf_counter()
    lod_glbs = generate_lod_glbs(name, normalized_glb, paths, tools, dynamic_plan["levels"])
    lod_sec = time.perf_counter() - lod_start

    b3dm_start = time.perf_counter()
    b3dm_map = {}
    for level in dynamic_plan["levels"]:
        level_name = level["name"]
        glb_path = lod_glbs[level_name]
        b3dm_path = os.path.join(output_dir, level_name, "content.b3dm")
        print(f"[Pipeline] {name} {level_name}: GLB -> b3dm")
        glb_to_b3dm(tools["tiles_tools_path"], glb_path, b3dm_path)
        b3dm_map[level_name] = b3dm_path
    b3dm_sec = time.perf_counter() - b3dm_start

    tileset_start = time.perf_counter()
    tileset_path = build_model_tileset(
        output_folder=output_dir,
        b3dm_map=b3dm_map,
        bbox=bbox,
        lon=lon,
        lat=lat,
        height=height,
        lod_plan=dynamic_plan["levels"],
    )
    tileset_sec = time.perf_counter() - tileset_start

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
        "lod_plan": dynamic_plan["levels"],
        "source_faces": dynamic_plan["original_faces"],
        "model_diagonal": dynamic_plan["model_diagonal"],
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


def write_bbox_json(output_dir, bbox, lod_plan=None):
    os.makedirs(output_dir, exist_ok=True)
    bbox_path = os.path.join(output_dir, "bbox.json")
    payload = {"bbox": bbox}
    if lod_plan is not None:
        payload["lod_plan"] = lod_plan
    with open(bbox_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return bbox_path
