import os
import shutil
import subprocess

from .tileset_builder import build_model_tileset, build_scene_tileset


LOD_TARGETS = {
    "lod3": 1.00,
    "lod2": 0.50,
    "lod1": 0.20,
    "lod0": 0.05,
}


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


def generate_lod_glbs(model_name, normalized_glb, paths, tools):
    lod_dir = os.path.join(paths["lod_dir"], model_name)
    shutil.rmtree(lod_dir, ignore_errors=True)
    os.makedirs(lod_dir, exist_ok=True)

    lod_paths = {}
    for level, ratio in LOD_TARGETS.items():
        level_dir = os.path.join(lod_dir, level)
        os.makedirs(level_dir, exist_ok=True)
        output_glb = os.path.join(level_dir, f"{model_name}.glb")

        if ratio >= 0.999:
            shutil.copy2(normalized_glb, output_glb)
            print(f"[LOD] {level}: copied full-detail GLB")
        else:
            print(f"[LOD] {level}: generating ratio {ratio:.2f}")
            run_blender_step(
                blender_path=tools["blender_path"],
                script_path=tools["blender_script"],
                mode="lod",
                input_path=normalized_glb,
                output_path=output_glb,
                extra_arg=ratio,
            )

        lod_paths[level] = output_glb

    return lod_paths


def build_model_artifacts(model, paths, tools):
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
    output_dir = os.path.join(paths["tiles_dir"], name)

    shutil.rmtree(output_dir, ignore_errors=True)

    print(f"[Pipeline] Normalizing {name} from {os.path.basename(source_path)}")
    run_blender_step(
        blender_path=tools["blender_path"],
        script_path=tools["blender_script"],
        mode="normalize",
        input_path=source_path,
        output_path=normalized_glb,
        extra_arg=unit,
    )

    bbox = read_bbox_file(bbox_path)
    if os.path.isfile(bbox_path):
        os.remove(bbox_path)

    lod_glbs = generate_lod_glbs(name, normalized_glb, paths, tools)

    b3dm_map = {}
    for level, glb_path in lod_glbs.items():
        b3dm_path = os.path.join(output_dir, level, "content.b3dm")
        print(f"[Pipeline] {name} {level}: GLB -> b3dm")
        glb_to_b3dm(tools["tiles_tools_path"], glb_path, b3dm_path)
        b3dm_map[level] = b3dm_path

    tileset_path = build_model_tileset(
        output_folder=output_dir,
        b3dm_map=b3dm_map,
        bbox=bbox,
        lon=lon,
        lat=lat,
        height=height,
    )
    if not tileset_path:
        raise RuntimeError("Failed to build model tileset")

    return {
        "bbox": bbox,
        "tileset_path": tileset_path,
    }


def rebuild_scene(config, paths):
    ready_models = []
    for model in config.get("models", []):
        if model.get("status") != "ready":
            continue
        model_tileset = os.path.join(paths["tiles_dir"], model["name"], "tileset.json")
        if not os.path.isfile(model_tileset):
            continue
        bbox_path = os.path.join(paths["tiles_dir"], model["name"], "bbox.json")
        bbox = {"width": 20.0, "depth": 20.0, "height": 10.0}
        if os.path.isfile(bbox_path):
            import json
            with open(bbox_path, "r", encoding="utf-8") as handle:
                bbox = json.load(handle)
        ready_models.append({**model, "_bbox": bbox})

    return build_scene_tileset(
        scene_dir=paths["scene_dir"],
        tiles_dir=paths["tiles_dir"],
        ready_models=ready_models,
    )


def write_bbox_json(output_dir, bbox):
    import json

    os.makedirs(output_dir, exist_ok=True)
    bbox_path = os.path.join(output_dir, "bbox.json")
    with open(bbox_path, "w", encoding="utf-8") as handle:
        json.dump(bbox, handle, indent=2)
    return bbox_path
