import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import open3d as o3d
import trimesh


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
    loaded = trimesh.load(
        source_path,
        force="scene",
        process=False,
        maintain_order=True,
    )

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

        if not isinstance(geom, trimesh.Trimesh):
            continue

        geom.apply_transform(transform)
        yield geom


def minimal_clean_trimesh(mesh):
    mesh = mesh.copy()
    mesh.vertices = np.asarray(mesh.vertices, dtype=np.float64)
    mesh.faces = np.asarray(mesh.faces, dtype=np.int64)

    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass

    try:
        mesh.remove_duplicate_faces()
    except Exception:
        pass

    try:
        mesh.remove_degenerate_faces()
    except Exception:
        pass

    return mesh


def scene_to_single_mesh_for_analysis(scene):
    meshes = []

    for mesh in iter_scene_meshes(scene):
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            continue
        meshes.append(minimal_clean_trimesh(mesh))

    if not meshes:
        raise RuntimeError("Scene contains no valid triangle meshes")

    return trimesh.util.concatenate(meshes)


def scene_to_single_mesh_for_decimation(scene):
    meshes = []

    for mesh in iter_scene_meshes(scene):
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            continue
        meshes.append(minimal_clean_trimesh(mesh))

    if not meshes:
        raise RuntimeError("Scene contains no valid triangle meshes")

    return trimesh.util.concatenate(meshes)


def scene_bounds(scene):
    bounds_list = []

    for mesh in iter_scene_meshes(scene):
        if len(mesh.vertices) > 0:
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
    output_dir = os.path.dirname(output_glb)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    scene.export(output_glb, file_type="glb")

    if not os.path.isfile(output_glb):
        raise RuntimeError(f"GLB export failed: {output_glb}")


def normalize_mesh(source_path, output_glb, unit="m"):
    print(f"[Mesh] Loading          : {source_path}")

    scene = load_scene(source_path)
    has_texture = scene_has_textures(scene)

    print(f"[Mesh] Texture detected : {'yes' if has_texture else 'no'}")

    scale = UNIT_SCALE.get(str(unit).lower(), 1.0)
    print(f"[Mesh] Unit scale       : {unit} -> meters, factor {scale}")

    raw_bounds = scene_bounds(scene)

    min_x_after_scale = float(raw_bounds[0][0] * scale)
    max_x_after_scale = float(raw_bounds[1][0] * scale)
    min_y_after_scale = float(raw_bounds[0][1] * scale)
    max_y_after_scale = float(raw_bounds[1][1] * scale)
    min_z_after_scale = float(raw_bounds[0][2] * scale)

    center_x_after_scale = (min_x_after_scale + max_x_after_scale) / 2.0
    center_y_after_scale = (min_y_after_scale + max_y_after_scale) / 2.0

    transform = np.eye(4)
    transform[0, 0] = scale
    transform[1, 1] = scale
    transform[2, 2] = scale
    transform[0, 3] = -center_x_after_scale
    transform[1, 3] = -center_y_after_scale
    transform[2, 3] = -min_z_after_scale

    scene.apply_transform(transform)

    analysis_mesh = scene_to_single_mesh_for_analysis(scene)
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
    return {
        "width": max(float(bounds[1][0] - bounds[0][0]), 0.001),
        "depth": max(float(bounds[1][1] - bounds[0][1]), 0.001),
        "height": max(float(bounds[1][2] - bounds[0][2]), 0.001),
    }


def analyze_mesh(mesh, source_path, bbox):
    faces = int(len(mesh.faces))
    vertices = int(len(mesh.vertices))
    diagonal = math.sqrt(
        bbox["width"] ** 2 + bbox["depth"] ** 2 + bbox["height"] ** 2
    )

    try:
        disconnected_parts = max(len(mesh.split(only_watertight=False)), 1)
    except Exception:
        disconnected_parts = 1

    try:
        volume_like = max(
            bbox["width"] * bbox["depth"] * bbox["height"],
            0.001,
        )
        geometric_complexity = min(1.0, float(mesh.area) / volume_like)
    except Exception:
        geometric_complexity = 0.0

    try:
        edges = mesh.edges_unique_length
        median_edge = float(np.median(edges)) if len(edges) > 0 else 0.0
    except Exception:
        median_edge = 0.0

    fine_detail_score = min(1.0, diagonal / max(median_edge * 500.0, 0.001))
    slenderness = max(
        [bbox["width"], bbox["depth"], bbox["height"]]
    ) / max(
        min([bbox["width"], bbox["depth"], bbox["height"]]),
        0.001,
    )

    return {
        "width": bbox["width"],
        "depth": bbox["depth"],
        "height": bbox["height"],
        "faces": faces,
        "vertices": vertices,
        "file_size_bytes": os.path.getsize(source_path),
        "disconnected_parts": disconnected_parts,
        "geometric_complexity": round(min(geometric_complexity, 1.0), 4),
        "fine_detail_score": round(min(fine_detail_score, 1.0), 4),
        "repeated_structure_score": 0.0,
        "slenderness": round(slenderness, 4),
        "diagonal": round(diagonal, 4),
    }


def decimate_with_pymeshlab(input_glb, output_glb, target_faces):
    """
    Use a subprocess for textured models so crashes or allocator issues in
    PyMeshLab do not take down the server process.
    """
    output_dir = os.path.dirname(output_glb)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    script = r"""
import os
import shutil
import sys
import tempfile

import pymeshlab
import trimesh


def load_scene(path):
    loaded = trimesh.load(path, force="scene", process=False)
    if isinstance(loaded, trimesh.Scene):
        return loaded
    scene = trimesh.Scene()
    scene.add_geometry(loaded, node_name="mesh")
    return scene


input_glb = sys.argv[1]
output_glb = sys.argv[2]
target_faces = int(sys.argv[3])

with tempfile.TemporaryDirectory() as tmp:
    png_glb = os.path.join(tmp, "png.glb")

    try:
        scene = load_scene(input_glb)
        for geom in scene.geometry.values():
            visual = getattr(geom, "visual", None)
            material = getattr(visual, "material", None) if visual else None
            image = getattr(material, "image", None) if material else None
            if image is not None and hasattr(image, "convert"):
                material.image = image.convert("RGBA")
        scene.export(png_glb, file_type="glb")
    except Exception:
        shutil.copy2(input_glb, png_glb)

    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(png_glb)

    for filter_name in [
        "meshing_remove_unreferenced_vertices",
        "meshing_remove_duplicate_faces",
        "meshing_remove_null_faces",
    ]:
        try:
            ms.apply_filter(filter_name)
        except Exception:
            pass

    used_texture = False
    try:
        ms.apply_filter(
            "meshing_decimation_quadric_edge_collapse_with_texture",
            targetfacenum=target_faces,
            preserveboundary=True,
            qualitythr=0.3,
            autoclean=True,
        )
        used_texture = True
    except Exception:
        ms.apply_filter(
            "meshing_decimation_quadric_edge_collapse",
            targetfacenum=target_faces,
            preservetopology=False,
            preserveboundary=True,
            optimalplacement=True,
            planarquadric=True,
            autoclean=True,
        )

    obj_path = os.path.join(tmp, "decimated.obj")
    ms.save_current_mesh(
        obj_path,
        save_textures=True,
        save_wedge_texcoord=True,
        save_vertex_normal=True,
        save_wedge_normal=True,
    )

    out_scene = load_scene(obj_path)
    tmp_out = output_glb + ".tmp"
    out_scene.export(tmp_out, file_type="glb")
    os.replace(tmp_out, output_glb)

    print("texture" if used_texture else "qem")
"""

    result = subprocess.run(
        [sys.executable, "-c", script, input_glb, output_glb, str(int(target_faces))],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0 or not os.path.isfile(output_glb):
        raise RuntimeError(
            result.stderr.strip() or f"Subprocess decimation failed for {input_glb}"
        )

    mode = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "qem"
    print(
        f"[Mesh] Decimation       : "
        f"{'texture-aware QEM' if mode == 'texture' else 'QEM'}"
    )
    return output_glb


def decimate_with_open3d(input_glb, output_glb, target_faces):
    """
    Fast geometry-only decimation for untextured models.
    """
    scene = load_scene(input_glb)
    mesh = scene_to_single_mesh_for_decimation(scene)

    o3d_mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(mesh.vertices),
        o3d.utility.Vector3iVector(mesh.faces),
    )

    target_faces = max(100, int(target_faces))
    current_faces = len(np.asarray(o3d_mesh.triangles))

    if target_faces < current_faces:
        o3d_mesh = o3d_mesh.simplify_quadric_decimation(target_faces)

    try:
        o3d_mesh = o3d_mesh.remove_unreferenced_vertices()
    except Exception:
        pass

    decimated_vertices = np.asarray(o3d_mesh.vertices)
    decimated_faces = np.asarray(o3d_mesh.triangles)

    decimated_mesh = trimesh.Trimesh(
        vertices=decimated_vertices,
        faces=decimated_faces,
        process=False,
    )
    decimated_scene = trimesh.Scene()
    decimated_scene.add_geometry(decimated_mesh, node_name="lod")

    output_dir = os.path.dirname(output_glb)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    decimated_scene.export(output_glb, file_type="glb")
    return output_glb


def optimize_glb(input_glb, output_glb, gltf_transform_path=None):
    output_dir = os.path.dirname(output_glb)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    tool = gltf_transform_path or shutil.which("gltf-transform")
    if not tool:
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] gltf-transform not found, copied without optimization")
        return output_glb

    result = subprocess.run(
        [tool, "optimize", input_glb, output_glb],
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0 or not os.path.isfile(output_glb):
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] gltf-transform optimize failed, copied original GLB instead")
        return output_glb

    print(f"[Mesh] Optimized GLB    : {output_glb}")
    return output_glb


def generate_lod_glb(input_glb, output_glb, ratio, target_faces, has_texture=False, tools=None):
    """
    Textured models:
    preserve source if decimation cannot safely keep materials.

    Untextured models:
    use Open3D decimation.
    """
    tools = tools or {}

    if ratio >= 0.999:
        output_dir = os.path.dirname(output_glb)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        shutil.copy2(input_glb, output_glb)
        print("[Mesh] Full LOD copied  : preserved original GLB")
        return output_glb

    glb_dir = os.path.dirname(output_glb)
    glb_base = os.path.splitext(os.path.basename(output_glb))[0]
    raw_lod = os.path.join(glb_dir, glb_base + "_raw.glb")

    try:
        if has_texture:
            print("[Mesh] Textured model   : routing through textured QEM subprocess")
            decimate_with_pymeshlab(input_glb, raw_lod, target_faces)

            if not glb_has_textures(raw_lod):
                raise RuntimeError("Decimated LOD lost texture/material data")
        else:
            print("[Mesh] Untextured model : routing through Open3D decimation")
            decimate_with_open3d(input_glb, raw_lod, target_faces)

        optimize_glb(
            raw_lod,
            output_glb,
            gltf_transform_path=tools.get("gltf_transform_path"),
        )
    except Exception as exc:
        output_dir = os.path.dirname(output_glb)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        shutil.copy2(input_glb, output_glb)
        print(f"[Mesh] Decimation fallback: copied source GLB for this LOD, reason: {exc}")
    finally:
        if os.path.isfile(raw_lod):
            os.remove(raw_lod)

    return output_glb
