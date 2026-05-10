import os

from .building_finder import find_buildings
from .crs_transform import build_transformers, transform_ring
from .exporter import export_obj, sanitize_building_id, write_manifest
from .geometry_extractor import choose_reference_point, extract_building_polygons
from .mesh_builder import build_local_mesh
from .reader import guess_srs_name, read_citygml


GML_NAMESPACE = "http://www.opengis.net/gml"


def _merge_global_bounds(bounds_acc, mesh_data):
    bbox = mesh_data["bbox"]
    offset_x = float(mesh_data["offset_x"])
    offset_y = float(mesh_data["offset_y"])
    offset_z = float(mesh_data["offset_z"])

    min_x = offset_x + float(bbox["min_x"]) - offset_x
    max_x = offset_x + float(bbox["max_x"]) - offset_x
    min_y = offset_y + float(bbox["min_y"]) - offset_y
    max_y = offset_y + float(bbox["max_y"]) - offset_y
    min_z = offset_z
    max_z = offset_z + float(bbox["height"])

    if bounds_acc is None:
        return {
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
            "min_z": min_z,
            "max_z": max_z,
        }

    bounds_acc["min_x"] = min(bounds_acc["min_x"], min_x)
    bounds_acc["max_x"] = max(bounds_acc["max_x"], max_x)
    bounds_acc["min_y"] = min(bounds_acc["min_y"], min_y)
    bounds_acc["max_y"] = max(bounds_acc["max_y"], max_y)
    bounds_acc["min_z"] = min(bounds_acc["min_z"], min_z)
    bounds_acc["max_z"] = max(bounds_acc["max_z"], max_z)
    return bounds_acc


def _finalize_bounds(bounds_acc):
    if bounds_acc is None:
        raise RuntimeError("No valid building geometry found in CityGML file")

    return {
        "min_x": bounds_acc["min_x"],
        "max_x": bounds_acc["max_x"],
        "min_y": bounds_acc["min_y"],
        "max_y": bounds_acc["max_y"],
        "min_z": bounds_acc["min_z"],
        "max_z": bounds_acc["max_z"],
        "width": bounds_acc["max_x"] - bounds_acc["min_x"],
        "depth": bounds_acc["max_y"] - bounds_acc["min_y"],
        "height": bounds_acc["max_z"] - bounds_acc["min_z"],
    }


def extract_citygml_buildings(gml_path, output_dir):
    _, root = read_citygml(gml_path)

    srs_name = guess_srs_name(root)
    if not srs_name:
        raise RuntimeError("Could not determine srsName from CityGML file")

    buildings = find_buildings(root)
    if not buildings:
        raise RuntimeError("No bldg:Building objects found in CityGML file")

    reference_point = choose_reference_point(buildings)
    transformers = build_transformers(srs_name, reference_point)
    to_local = transformers["to_local"]

    os.makedirs(output_dir, exist_ok=True)

    building_records = []
    global_bounds = None

    for index, building in enumerate(buildings):
        polygons = extract_building_polygons(building)
        if not polygons:
            continue

        transformed_polygons = []
        for polygon in polygons:
            local_ring = transform_ring(polygon, to_local)
            if len(local_ring) >= 3:
                transformed_polygons.append(local_ring)

        mesh_data = build_local_mesh(transformed_polygons)
        if not mesh_data:
            continue

        building_id = (
            building.attrib.get(f"{{{GML_NAMESPACE}}}id")
            or f"building_{index:05d}"
        )

        file_name = f"{sanitize_building_id(building_id)}.obj"
        output_path = os.path.join(output_dir, file_name)

        export_obj(output_path, mesh_data["vertices"], mesh_data["faces"])

        building_records.append({
            "name": os.path.splitext(file_name)[0],
            "file": file_name,
            "offset_x": round(float(mesh_data["offset_x"]), 4),
            "offset_y": round(float(mesh_data["offset_y"]), 4),
            "offset_z": round(float(mesh_data["offset_z"]), 4),
            "bbox": {
                "width": round(float(mesh_data["bbox"]["width"]), 4),
                "depth": round(float(mesh_data["bbox"]["depth"]), 4),
                "height": round(float(mesh_data["bbox"]["height"]), 4),
            },
        })

        global_bounds = _merge_global_bounds(global_bounds, mesh_data)

    if not building_records:
        raise RuntimeError("No valid building meshes could be extracted from CityGML")

    overall = _finalize_bounds(global_bounds)
    root_height = overall["min_z"]

    for record in building_records:
        record["offset_z"] = round(float(record["offset_z"]) - root_height, 4)

    manifest = {
        "srs_name": srs_name,
        "origin_lon": transformers["origin_lon"],
        "origin_lat": transformers["origin_lat"],
        "origin_height": round(root_height, 4),
        "bbox": {
            "width": round(float(overall["width"]), 4),
            "depth": round(float(overall["depth"]), 4),
            "height": round(float(overall["height"]), 4),
        },
        "buildings": building_records,
    }

    write_manifest(output_dir, manifest)
    return manifest
