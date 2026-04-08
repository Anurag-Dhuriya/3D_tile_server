import json
import os
import sys

import bpy


SCALE_FACTORS = {
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "ft": 0.3048,
    "in": 0.0254,
}


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_model(input_path):
    ext = os.path.splitext(input_path)[1].lower()

    if ext == ".obj":
        bpy.ops.wm.obj_import(
            filepath=input_path,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
        return

    if ext in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=input_path)
        return

    raise RuntimeError(f"Unsupported import format: {ext}")


def select_mesh_objects():
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not objects:
        raise RuntimeError("No mesh objects found after import")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    return objects


def get_active_mesh():
    objects = select_mesh_objects()
    if len(objects) > 1:
        bpy.ops.object.join()
    return bpy.context.active_object


def apply_scale_to_meters(obj, scale_unit):
    scale = SCALE_FACTORS.get(scale_unit, 1.0)
    if scale != 1.0:
        print(f"[Blender] Scaling: {scale_unit} -> meters (factor {scale})")
        obj.scale = (scale, scale, scale)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def clean_geometry(obj):
    print("[Blender] Cleaning geometry...")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def align_to_base_center(obj):
    bbox = obj.bound_box
    xs = [v[0] for v in bbox]
    ys = [v[1] for v in bbox]
    zs = [v[2] for v in bbox]

    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    min_z = min(zs)

    obj.location.x -= center_x
    obj.location.y -= center_y
    obj.location.z -= min_z
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)


def mesh_metrics(obj):
    bbox = obj.bound_box
    xs = [v[0] for v in bbox]
    ys = [v[1] for v in bbox]
    zs = [v[2] for v in bbox]
    width = max(xs) - min(xs)
    depth = max(ys) - min(ys)
    height = max(zs) - min(zs)
    return {
        "width": width,
        "depth": depth,
        "height": height,
        "base_z": min(zs),
    }


def write_bbox_file(output_path, metrics):
    bbox_path = output_path.replace(".glb", "_bbox.txt")
    with open(bbox_path, "w", encoding="utf-8") as handle:
        handle.write(f"{metrics['width']},{metrics['depth']},{metrics['height']}")
    print(f"[Blender] Bounding box written -> {bbox_path}")


def write_meta_file(output_path, metrics, obj):
    meta_path = output_path.replace(".glb", "_meta.json")
    payload = {
        "width": metrics["width"],
        "depth": metrics["depth"],
        "height": metrics["height"],
        "base_z": metrics["base_z"],
        "vertices": len(obj.data.vertices),
        "faces": len(obj.data.polygons),
    }
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"[Blender] Metadata written -> {meta_path}")


def export_glb(output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_apply=True,
        export_normals=True,
        export_texcoords=True,
        export_materials="EXPORT",
        export_cameras=False,
        export_lights=False,
        use_selection=False,
    )
    if not os.path.isfile(output_path):
        raise RuntimeError(f"GLB not found after export: {output_path}")


def run_normalize(input_path, output_path, scale_unit):
    print(f"[Blender] Mode            : normalize")
    print(f"[Blender] Input           : {input_path}")
    print(f"[Blender] Output          : {output_path}")
    print(f"[Blender] Unit            : {scale_unit}")

    clear_scene()
    import_model(input_path)
    obj = get_active_mesh()
    apply_scale_to_meters(obj, scale_unit)
    clean_geometry(obj)
    align_to_base_center(obj)

    metrics = mesh_metrics(obj)
    print(f"[Blender] Width           : {metrics['width']:.3f}m")
    print(f"[Blender] Depth           : {metrics['depth']:.3f}m")
    print(f"[Blender] Height          : {metrics['height']:.3f}m")
    print(f"[Blender] Base Z          : {metrics['base_z']:.4f}m")

    write_bbox_file(output_path, metrics)
    write_meta_file(output_path, metrics, obj)
    export_glb(output_path)
    print("[Blender] Normalize complete")


def apply_decimate_modifier(obj, ratio):
    ratio = max(0.0, min(1.0, ratio))
    if ratio >= 0.999:
        return

    modifier = obj.modifiers.new(name="LODDecimate", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def run_lod(input_path, output_path, ratio):
    print(f"[Blender] Mode            : lod")
    print(f"[Blender] Input           : {input_path}")
    print(f"[Blender] Output          : {output_path}")
    print(f"[Blender] Ratio           : {ratio}")

    clear_scene()
    import_model(input_path)
    obj = get_active_mesh()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    apply_decimate_modifier(obj, float(ratio))
    align_to_base_center(obj)
    export_glb(output_path)
    print("[Blender] LOD export complete")


def parse_args(argv):
    try:
        idx = argv.index("--")
        mode = argv[idx + 1]
        input_path = argv[idx + 2]
        output_path = argv[idx + 3]
        extra = argv[idx + 4]
        return mode, input_path, output_path, extra
    except (ValueError, IndexError):
        raise RuntimeError(
            "Usage: blender --background --python blender_process.py -- "
            "<normalize|lod> <input> <output> <unit_or_ratio>"
        )


def main():
    mode, input_path, output_path, extra = parse_args(sys.argv)

    if not os.path.isfile(input_path):
        raise RuntimeError(f"Input file not found: {input_path}")

    print(f"[Blender] Blender version : {bpy.app.version_string}")

    if mode == "normalize":
        run_normalize(input_path, output_path, extra)
        return

    if mode == "lod":
        run_lod(input_path, output_path, float(extra))
        return

    raise RuntimeError(f"Unknown mode: {mode}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[Blender] ERROR: {exc}")
        sys.exit(1)
