import math
import os

import numpy as np
import trimesh

from .mesh_backend import load_scene, iter_scene_meshes


def should_spatially_chunk(meta, bbox, mode="auto"):
    if mode is True:
        return True

    if str(mode).lower() in {"false", "off", "none", "no"}:
        return False

    faces = int(meta.get("faces", 0))
    width = float(bbox.get("width", 0.0))
    depth = float(bbox.get("depth", 0.0))
    diagonal = float(meta.get("diagonal", math.sqrt(width * width + depth * depth)))

    if faces >= 150000:
        return True

    if width >= 200 or depth >= 200:
        return True

    if diagonal >= 300:
        return True

    return False


def cell_key_from_xy(x, y, min_x, min_y, chunk_size):
    ix = int(math.floor((x - min_x) / chunk_size))
    iy = int(math.floor((y - min_y) / chunk_size))
    return ix, iy


def scene_bounds(scene):
    bounds = []

    for mesh in iter_scene_meshes(scene):
        if len(mesh.vertices) > 0 and len(mesh.faces) > 0:
            bounds.append(mesh.bounds)

    if not bounds:
        raise RuntimeError("Cannot spatially chunk empty scene")

    mins = np.min([item[0] for item in bounds], axis=0)
    maxs = np.max([item[1] for item in bounds], axis=0)

    return np.array([mins, maxs], dtype=np.float64)


def collect_cell_meshes(scene, chunk_size):
    bounds = scene_bounds(scene)

    min_x = float(bounds[0][0])
    min_y = float(bounds[0][1])

    cells = {}

    for mesh_index, mesh in enumerate(iter_scene_meshes(scene)):
        if len(mesh.faces) == 0:
            continue

        centroids = mesh.triangles_center

        face_groups = {}

        for face_index, centroid in enumerate(centroids):
            key = cell_key_from_xy(
                x=float(centroid[0]),
                y=float(centroid[1]),
                min_x=min_x,
                min_y=min_y,
                chunk_size=chunk_size,
            )
            face_groups.setdefault(key, []).append(face_index)

        for key, face_indices in face_groups.items():
            if not face_indices:
                continue

            try:
                submesh = mesh.submesh(
                    [face_indices],
                    append=True,
                    repair=False,
                )
            except TypeError:
                submesh = mesh.submesh(
                    [face_indices],
                    append=True,
                )

            if len(submesh.vertices) == 0 or len(submesh.faces) == 0:
                continue

            cells.setdefault(key, []).append(submesh)

    return cells


def combined_bounds(meshes):
    bounds = []

    for mesh in meshes:
        if len(mesh.vertices) > 0:
            bounds.append(mesh.bounds)

    mins = np.min([item[0] for item in bounds], axis=0)
    maxs = np.max([item[1] for item in bounds], axis=0)

    return np.array([mins, maxs], dtype=np.float64)


def export_cell_glb(cell_meshes, output_path, offset):
    scene = trimesh.Scene()

    for index, mesh in enumerate(cell_meshes):
        local_mesh = mesh.copy()
        local_mesh.apply_translation(-offset)
        scene.add_geometry(local_mesh, node_name=f"part_{index:03d}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    scene.export(output_path, file_type="glb")

    if not os.path.isfile(output_path):
        raise RuntimeError(f"Failed to export chunk GLB: {output_path}")


def chunk_scene_to_glb_assets(
    source_glb,
    output_dir,
    chunk_size_m=5000.0,  # <--- INCREASED FROM 150.0
    max_chunks=256,       # <--- INCREASED FROM 128 (Optional, gives you more headroom)
    min_faces_per_chunk=50,
):
    scene = load_scene(source_glb)
    cells = collect_cell_meshes(scene, chunk_size_m)

    if not cells:
        raise RuntimeError("Spatial chunking produced no cells")

    if len(cells) > max_chunks:
        raise RuntimeError(
            f"Spatial chunking produced {len(cells)} chunks, "
            f"which is above max_chunks={max_chunks}. "
            f"Increase chunk_size_m or max_chunks."
        )

    os.makedirs(output_dir, exist_ok=True)

    records = []

    for ordinal, key in enumerate(sorted(cells.keys())):
        meshes = cells[key]
        face_count = sum(len(mesh.faces) for mesh in meshes)

        if face_count < min_faces_per_chunk:
            continue

        bounds = combined_bounds(meshes)

        offset = np.array(
            [
                float(bounds[0][0]),
                float(bounds[0][1]),
                float(bounds[0][2]),
            ],
            dtype=np.float64,
        )

        width = max(float(bounds[1][0] - bounds[0][0]), 0.001)
        depth = max(float(bounds[1][1] - bounds[0][1]), 0.001)
        height = max(float(bounds[1][2] - bounds[0][2]), 0.001)

        ix, iy = key
        chunk_name = f"chunk_{ordinal:04d}_x{ix}_y{iy}"
        output_path = os.path.join(output_dir, f"{chunk_name}.glb")

        export_cell_glb(meshes, output_path, offset)

        records.append({
            "name": chunk_name,
            "file_path": output_path,
            "offset_x": round(float(offset[0]), 4),
            "offset_y": round(float(offset[1]), 4),
            "offset_z": round(float(offset[2]), 4),
            "bbox": {
                "width": round(width, 4),
                "depth": round(depth, 4),
                "height": round(height, 4),
            },
            "faces": int(face_count),
            "cell_x": int(ix),
            "cell_y": int(iy),
        })

    if not records:
        raise RuntimeError("Spatial chunking produced no usable chunk files")

    print(
        f"[Chunking] {os.path.basename(source_glb)} -> "
        f"{len(records)} spatial chunk(s), chunk_size={chunk_size_m}m"
    )

    return records
