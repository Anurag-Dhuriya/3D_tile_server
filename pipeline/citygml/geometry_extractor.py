GML_NAMESPACE = "http://www.opengis.net/gml"


def local_name(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_poslist(text, srs_dimension=None):
    values = [float(value) for value in text.strip().split() if value.strip()]
    if not values:
        return []

    if srs_dimension is not None:
        dimension = int(srs_dimension)
    else:
        dimension = 3 if len(values) % 3 == 0 else 2

    points = []
    for index in range(0, len(values), dimension):
        coords = values[index:index + dimension]
        if len(coords) < 2:
            continue
        x = coords[0]
        y = coords[1]
        z = coords[2] if len(coords) >= 3 else 0.0
        points.append((x, y, z))
    return points


def parse_ring(linear_ring):
    pos_list = linear_ring.find(f".//{{{GML_NAMESPACE}}}posList")
    if pos_list is not None and (pos_list.text or "").strip():
        return parse_poslist(
            pos_list.text,
            pos_list.attrib.get("srsDimension") or linear_ring.attrib.get("srsDimension"),
        )

    coords = []
    for pos in linear_ring.findall(f".//{{{GML_NAMESPACE}}}pos"):
        if (pos.text or "").strip():
            coords.extend(parse_poslist(pos.text, pos.attrib.get("srsDimension")))
    return coords


def extract_building_polygons(building):
    polygons = []

    for polygon in building.iter():
        if local_name(polygon.tag) != "Polygon":
            continue

        exterior = polygon.find(f"./{{{GML_NAMESPACE}}}exterior")
        if exterior is None:
            continue

        linear_ring = exterior.find(f"./{{{GML_NAMESPACE}}}LinearRing")
        if linear_ring is None:
            continue

        ring = parse_ring(linear_ring)
        if len(ring) >= 3:
            if ring[0] == ring[-1]:
                ring = ring[:-1]
            if len(ring) >= 3:
                polygons.append(ring)

    return polygons


def choose_reference_point(buildings):
    for building in buildings:
        polygons = extract_building_polygons(building)
        if polygons and polygons[0]:
            return polygons[0][0]
    raise RuntimeError("No building geometry found in CityGML file")
