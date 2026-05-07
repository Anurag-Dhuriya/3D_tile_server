def triangulate_polygon(ring):
    if len(ring) < 3:
        return []

    faces = []
    for index in range(1, len(ring) - 1):
        faces.append((0, index, index + 1))
    return faces


def bbox_from_points(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]

    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "min_z": min(zs),
        "max_z": max(zs),
        "width": max(xs) - min(xs),
        "depth": max(ys) - min(ys),
        "height": max(zs) - min(zs),
    }


def build_local_mesh(transformed_polygons):
    building_points = []
    for polygon in transformed_polygons:
        building_points.extend(polygon)

    if not building_points:
        return None

    bbox = bbox_from_points(building_points)
    center_x = (bbox["min_x"] + bbox["max_x"]) / 2.0
    center_y = (bbox["min_y"] + bbox["max_y"]) / 2.0
    min_z = bbox["min_z"]

    vertices = []
    faces = []

    for ring in transformed_polygons:
        local_vertices = [
            (x - center_x, y - center_y, z - min_z)
            for x, y, z in ring
        ]
        start_index = len(vertices)
        vertices.extend(local_vertices)

        for face in triangulate_polygon(local_vertices):
            faces.append((
                start_index + face[0],
                start_index + face[1],
                start_index + face[2],
            ))

    if not faces:
        return None

    return {
        "vertices": vertices,
        "faces": faces,
        "bbox": bbox,
        "offset_x": center_x,
        "offset_y": center_y,
        "offset_z": min_z,
        # Removed "points": building_points — redundant copy of pre-centered
        # geometry. bbox, offset_*, and vertices already capture everything needed.
    }