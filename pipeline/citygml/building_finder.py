BUILDING_NAMESPACES = (
    "http://www.opengis.net/citygml/building/2.0",
    "http://www.opengis.net/citygml/building/1.0",
)


def local_name(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def namespace_uri(tag):
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def find_buildings(root):
    buildings = []
    for element in root.iter():
        if local_name(element.tag) not in {"Building", "BuildingPart"}:
            continue
        if namespace_uri(element.tag) not in BUILDING_NAMESPACES:
            continue
        buildings.append(element)
    return buildings
