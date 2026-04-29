import json
import os


def sanitize_building_id(building_id):
    return building_id.replace(":", "_").replace("/", "_")


def export_obj(path, vertices, faces):
    with open(path, "w", encoding="utf-8") as handle:
        for vx, vy, vz in vertices:
            handle.write(f"v {vx:.6f} {vy:.6f} {vz:.6f}\n")
        for a, b, c in faces:
            handle.write(f"f {a + 1} {b + 1} {c + 1}\n")


def write_manifest(output_dir, manifest):
    manifest_path = os.path.join(output_dir, "citygml_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest_path
