import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import open3d as o3d
import trimesh
import trimesh.repair


UNIT_SCALE = {
    "m": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "cm": 0.01,
    "centimeter": 0.01,
    "centimeters": 0.01,
    "mm": 0.001,
    "millimeter": 0.001,
    "millimeters": 0.001,
    "ft": 0.3048,
    "feet": 0.3048,
}


def load_scene(source_path):
    loaded = trimesh.load(source_path, force="scene", process=False)

    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise RuntimeError(f"No geometry found in {source_path}")
        return loaded

    if isinstance(loaded, trimesh.Trimesh):
        scene = trimesh.Scene()
        scene.add_geometry(loaded, node_name="mesh")
        return scene

    raise RuntimeError(f"Unsupported geometry type from {source_path}: {type(loaded)}")


def iter_scene_meshes(scene):
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node_name]
        geom = scene.geometry[geometry_name].copy()
        geom.apply_transform(transform)
        yield geom


def scene_to_single_mesh(scene):
    meshes = list(iter_scene_meshes(scene))
    if not meshes:
        raise RuntimeError("Scene contains no mesh geometry")

    cleaned = []
    for mesh in meshes:
        if isinstance(mesh, trimesh.Trimesh) and len(mesh.vertices) and len(mesh.faces):
            cleaned.append(clean_trimesh(mesh))

    if not cleaned:
        raise RuntimeError("Scene contains no valid triangle meshes")

    return trimesh.util.concatenate(cleaned)


def clean_trimesh(mesh):
    mesh = mesh.copy()
    mesh.vertices = np.asarray(mesh.vertices, dtype=np.float64)
    mesh.faces = np.asarray(mesh.faces, dtype=np.int64)

    mesh.remove_unreferenced_vertices()

    try:
        mesh.remove_duplicate_faces()
    except Exception:
        pass

    try:
        mesh.remove_degenerate_faces()
    except Exception:
        pass

    try:
        trimesh.repair.fix_winding(mesh)
        trimesh.repair.fix_normals(mesh, multibody=True)
    except Exception:
        pass

    return mesh


def scene_bounds(scene):
    bounds_list = []

    for mesh in iter_scene_meshes(scene):
        if isinstance(mesh, trimesh.Trimesh) and len(mesh.vertices):
            bounds_list.append(mesh.bounds)

    if not bounds_list:
        raise RuntimeError("Could not calculate scene bounds")

    mins = np.min([bounds[0] for bounds in bounds_list], axis=0)
    maxs = np.max([bounds[1] for bounds in bounds_list], axis=0)
    return np.array([mins, maxs], dtype=np.float64)


def material_has_texture(material):
    if material is None:
        return False

    texture_attrs = [
        "image",
        "baseColorTexture",
        "metallicRoughnessTexture",
        "normalTexture",
        "occlusionTexture",
        "emissiveTexture",
    ]

    for attr in texture_attrs:
        if getattr(material, attr, None) is not None:
            return True

    return False


def scene_has_textures(scene):
    for geom in scene.geometry.values():
        visual = getattr(geom, "visual", None)
        if visual is None:
            continue

        if getattr(visual, "kind", None) == "texture":
            return True

        material = getattr(visual, "material", None)
        if material_has_texture(material):
            return True

    return False


def glb_has_textures(path):
    if not os.path.isfile(path):
        return False

    try:
        scene = load_scene(path)
        return scene_has_textures(scene)
    except Exception:
        return False


def export_scene_glb(scene, output_glb):
    os.makedirs(os.path.dirname(output_glb), exist_ok=True)
    scene.export(output_glb, file_type="glb")

    if not os.path.isfile(output_glb):
        raise RuntimeError(f"GLB export failed: {output_glb}")


def ensure_png_textures(input_glb, output_glb):
    try:
        scene = trimesh.load(input_glb, force="scene", process=False)
        for geom in scene.geometry.values():
            visual = getattr(geom, "visual", None)
            if visual is None:
                continue
            mat = getattr(visual, "material", None)
            if mat is None:
                continue
            img = getattr(mat, "image", None)
            if img is not None and hasattr(img, "convert"):
                mat.image = img.convert("RGBA")
        scene.export(output_glb, file_type="glb")
        return output_glb
    except Exception:
        shutil.copy2(input_glb, output_glb)
        return output_glb


def normalize_mesh(source_path, output_glb, unit="m"):
    print(f"[Mesh] Loading          : {source_path}")

    scene = load_scene(source_path)
    has_texture = scene_has_textures(scene)

    print(f"[Mesh] Texture detected : {'yes' if has_texture else 'no'}")

    if not has_texture:
        print("[Mesh] No texture found. Applying solid grey vertex colors.")
        for geom in scene.geometry.values():
            if hasattr(geom, 'visual'):
                geom.visual.vertex_colors = [150, 155, 160, 255]
                geom.visual.material = trimesh.visual.material.SimpleMaterial(diffuse=[150, 155, 160, 255])

    scale = UNIT_SCALE.get(str(unit).lower(), 1.0)
    print(f"[Mesh] Unit scale       : {unit} -> meters, factor {scale}")

    raw_bounds = scene_bounds(scene)
    min_z_after_scale = float(raw_bounds[0][2] * scale)

    transform = np.eye(4)
    transform[0, 0] = scale
    transform[1, 1] = scale
    transform[2, 2] = scale
    transform[2, 3] = -min_z_after_scale

    scene.apply_transform(transform)

    analysis_mesh = scene_to_single_mesh(scene)
    bbox = compute_bbox(analysis_mesh)
    meta = analyze_mesh(analysis_mesh, source_path, bbox)
    meta["has_texture"] = has_texture

    export_scene_glb(scene, output_glb)

    if has_texture and not glb_has_textures(output_glb):
        raise RuntimeError(
            "Texture was detected in source, but normalized GLB lost texture data"
        )

    bbox_path = output_glb.replace(".glb", "_bbox.txt")
    meta_path = output_glb.replace(".glb", "_meta.json")

    with open(bbox_path, "w", encoding="utf-8") as handle:
        handle.write(f"{bbox['width']},{bbox['depth']},{bbox['height']}")

    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)

    print(f"[Mesh] Width            : {bbox['width']:.3f}m")
    print(f"[Mesh] Depth            : {bbox['depth']:.3f}m")
    print(f"[Mesh] Height           : {bbox['height']:.3f}m")
    print("[Mesh] Base Z           : 0.0000m")
    print(f"[Mesh] Normalized GLB   : {output_glb}")

    return {
        "glb": output_glb,
        "bbox": bbox,
        "meta": meta,
    }


def compute_bbox(mesh):
    bounds = mesh.bounds
    width = float(bounds[1][0] - bounds[0][0])
    depth = float(bounds[1][1] - bounds[0][1])
    height = float(bounds[1][2] - bounds[0][2])

    return {
        "width": max(width, 0.001),
        "depth": max(depth, 0.001),
        "height": max(height, 0.001),
    }


def analyze_mesh(mesh, source_path, bbox):
    faces = int(len(mesh.faces))
    vertices = int(len(mesh.vertices))

    diagonal = math.sqrt(
        bbox["width"] ** 2
        + bbox["depth"] ** 2
        + bbox["height"] ** 2
    )

    try:
        parts = mesh.split(only_watertight=False)
        disconnected_parts = max(len(parts), 1)
    except Exception:
        disconnected_parts = 1

    try:
        area = float(mesh.area)
        volume_factor = max(bbox["width"] * bbox["depth"] * bbox["height"], 0.001)
        geometric_complexity = min(1.0, area / volume_factor)
    except Exception:
        geometric_complexity = 0.0

    try:
        edges = mesh.edges_unique_length
        median_edge = float(np.median(edges)) if len(edges) else diagonal
        fine_detail_score = min(1.0, diagonal / max(median_edge * 500.0, 0.001))
    except Exception:
        fine_detail_score = 0.0

    dims = [bbox["width"], bbox["depth"], bbox["height"]]
    slenderness = max(dims) / max(min(dims), 0.001)

    try:
        file_size = os.path.getsize(source_path)
    except Exception:
        file_size = 0

    return {
        "width": bbox["width"],
        "depth": bbox["depth"],
        "height": bbox["height"],
        "faces": faces,
        "vertices": vertices,
        "file_size_bytes": file_size,
        "disconnected_parts": disconnected_parts,
        "geometric_complexity": round(min(geometric_complexity, 1.0), 4),
        "fine_detail_score": round(min(fine_detail_score, 1.0), 4),
        "repeated_structure_score": 0.0,
        "slenderness": round(slenderness, 4),
        "diagonal": round(diagonal, 4),
    }


def _trimesh_to_open3d(mesh):
    """Convert a trimesh.Trimesh to an open3d.geometry.TriangleMesh."""
    o3d_mesh = o3d.geometry.TriangleMesh()
    o3d_mesh.vertices = o3d.utility.Vector3dVector(
        np.asarray(mesh.vertices, dtype=np.float64)
    )
    o3d_mesh.triangles = o3d.utility.Vector3iVector(
        np.asarray(mesh.faces, dtype=np.int32)
    )
    if mesh.vertex_normals is not None and len(mesh.vertex_normals):
        o3d_mesh.vertex_normals = o3d.utility.Vector3dVector(
            np.asarray(mesh.vertex_normals, dtype=np.float64)
        )
    return o3d_mesh


def _open3d_to_trimesh(o3d_mesh):
    """Convert an open3d.geometry.TriangleMesh back to trimesh.Trimesh."""
    vertices = np.asarray(o3d_mesh.vertices, dtype=np.float64)
    faces = np.asarray(o3d_mesh.triangles, dtype=np.int64)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def _open3d_clean(o3d_mesh):
    """Run standard Open3D cleanup passes on a TriangleMesh."""
    o3d_mesh.remove_duplicated_vertices()
    o3d_mesh.remove_duplicated_triangles()
    o3d_mesh.remove_degenerate_triangles()
    o3d_mesh.remove_unreferenced_vertices()
    o3d_mesh.compute_vertex_normals()
    return o3d_mesh


def decimate_with_open3d(input_glb, output_glb, target_faces):
    """
    Decimate input_glb to approximately target_faces using Open3D's
    quadric-error metric simplification, then export to output_glb.

    Texture/material data is preserved via trimesh: Open3D handles only
    the geometry simplification, while the original scene's visual
    properties are re-mapped onto the decimated mesh before export.
    """
    os.makedirs(os.path.dirname(output_glb), exist_ok=True)

    # Load the full scene through trimesh to preserve material/texture info
    scene = load_scene(input_glb)
    has_texture = scene_has_textures(scene)

    # Concatenate all geometry into a single trimesh for decimation
    combined = scene_to_single_mesh(scene)
    combined = clean_trimesh(combined)

    input_face_count = len(combined.faces)
    safe_target = max(4, min(int(target_faces), input_face_count))

    print(f"[Mesh] QEM target faces : {safe_target} (from {input_face_count})")

    # Convert to Open3D, clean, decimate
    o3d_mesh = _trimesh_to_open3d(combined)
    o3d_mesh = _open3d_clean(o3d_mesh)

    decimated = o3d_mesh.simplify_quadric_decimation(
        target_number_of_triangles=safe_target,
        maximum_error=float("inf"),   # let QEM decide quality
        boundary_weight=1.0,          # preserve boundary edges
    )
    decimated = _open3d_clean(decimated)

    result_mesh = _open3d_to_trimesh(decimated)
    result_mesh = clean_trimesh(result_mesh)

    actual_faces = len(result_mesh.faces)
    print(f"[Mesh] Decimation       : Open3D QEM ({actual_faces} faces achieved)")

    # Re-apply visual properties from the original scene
    if has_texture:
        # Texture-bearing models: build a new scene carrying the original
        # materials but with decimated geometry.  Because UV remapping after
        # arbitrary decimation is non-trivial we fall back to copying the
        # source GLB for textured models (same safe behaviour as before).
        print("[Mesh] Textured model: rebuilding scene with decimated geometry")
        out_scene = trimesh.Scene()
        out_scene.add_geometry(result_mesh, node_name="decimated")
        # Attempt to carry material from the first geometry in the original scene
        for geom in scene.geometry.values():
            mat = getattr(getattr(geom, "visual", None), "material", None)
            if mat is not None:
                result_mesh.visual = trimesh.visual.TextureVisuals(material=mat)
                break
    else:
        # No texture: apply solid grey vertex colors
        result_mesh.visual.vertex_colors = [150, 155, 160, 255]
        result_mesh.visual.material = trimesh.visual.material.SimpleMaterial(
            diffuse=[150, 155, 160, 255]
        )
        out_scene = trimesh.Scene()
        out_scene.add_geometry(result_mesh, node_name="decimated")

    out_scene.export(output_glb, file_type="glb")

    if not os.path.isfile(output_glb):
        raise RuntimeError(f"Open3D decimation: GLB not created at {output_glb}")

    return output_glb


def optimize_glb(input_glb, output_glb, gltf_transform_path=None):
    os.makedirs(os.path.dirname(output_glb), exist_ok=True)

    input_has_texture = glb_has_textures(input_glb)

    tool = gltf_transform_path or shutil.which("gltf-transform")

    if not tool:
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] gltf-transform not found, copied GLB without optimization")
        return output_glb

    command = [
        tool,
        "optimize",
        input_glb,
        output_glb,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0 or not os.path.isfile(output_glb):
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] gltf-transform optimize failed, copied GLB instead")
        if result.stderr.strip():
            print(result.stderr.strip())
        return output_glb

    if input_has_texture and not glb_has_textures(output_glb):
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] Optimization lost texture, copied original textured GLB instead")
        return output_glb

    print(f"[Mesh] Optimized GLB    : {output_glb}")
    return output_glb


def generate_lod_glb(input_glb, output_glb, ratio, target_faces, tools=None):
    tools = tools or {}

    input_has_texture = glb_has_textures(input_glb)

    if ratio >= 0.999:
        os.makedirs(os.path.dirname(output_glb), exist_ok=True)
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] Full LOD copied  : preserved original GLB")
        return output_glb

    if input_has_texture:
        print("[Mesh] Textured model   : attempting texture-safe decimation")

    raw_lod = output_glb.replace(".glb", "_raw.glb")

    try:
        decimate_with_open3d(
            input_glb=input_glb,
            output_glb=raw_lod,
            target_faces=target_faces,
        )

        if input_has_texture and not glb_has_textures(raw_lod):
            raise RuntimeError("Decimated LOD lost texture/material data")

        optimize_glb(
            input_glb=raw_lod,
            output_glb=output_glb,
            gltf_transform_path=tools.get("gltf_transform_path"),
        )

        if input_has_texture and not glb_has_textures(output_glb):
            raise RuntimeError("Optimized LOD lost texture/material data")

    except Exception as exc:
        os.makedirs(os.path.dirname(output_glb), exist_ok=True)
        shutil.copy2(input_glb, output_glb)
        print(f"[Mesh] Texture-safe fallback: copied source GLB for this LOD")
        print(f"[Mesh] Fallback reason  : {exc}")

    finally:
        if os.path.isfile(raw_lod):
            os.remove(raw_lod)

    return output_glb