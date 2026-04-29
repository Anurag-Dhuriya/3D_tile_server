import os

from .building_finder import find_buildings
from .crs_transform import build_transformers, transform_ring
from .exporter import export_obj, sanitize_building_id, write_manifest
from .geometry_extractor import choose_reference_point, extract_building_polygons
from .mesh_builder import bbox_from_points, build_local_mesh
from .reader import guess_srs_name, read_citygml


GML_NAMESPACE = "http://www.opengis.net/gml"


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
    all_points = []

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
            "offset_x": round(mesh_data["offset_x"], 4),
            "offset_y": round(mesh_data["offset_y"], 4),
            "offset_z": round(mesh_data["offset_z"], 4),
            "bbox": {
                "width": round(mesh_data["bbox"]["width"], 4),
                "depth": round(mesh_data["bbox"]["depth"], 4),
                "height": round(mesh_data["bbox"]["height"], 4),
            },
        })

        all_points.extend(mesh_data["points"])

    if not building_records:
        raise RuntimeError("No valid building meshes could be extracted from CityGML")

    overall = bbox_from_points(all_points)
    root_height = overall["min_z"]

    for record in building_records:
        record["offset_z"] = round(record["offset_z"] - root_height, 4)

    manifest = {
        "srs_name": srs_name,
        "origin_lon": transformers["origin_lon"],
        "origin_lat": transformers["origin_lat"],
        "origin_height": round(root_height, 4),
        "bbox": {
            "width": round(overall["width"], 4),
            "depth": round(overall["depth"], 4),
            "height": round(overall["height"], 4),
        },
        "buildings": building_records,
    }

    write_manifest(output_dir, manifest)
    return manifest
