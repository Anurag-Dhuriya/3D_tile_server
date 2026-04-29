import xml.etree.ElementTree as ET


GML_NAMESPACE = "http://www.opengis.net/gml"


def read_citygml(gml_path):
    tree = ET.parse(gml_path)
    root = tree.getroot()
    return tree, root


def guess_srs_name(root):
    if "srsName" in root.attrib:
        return root.attrib["srsName"]

    envelope = root.find(f".//{{{GML_NAMESPACE}}}Envelope")
    if envelope is not None and "srsName" in envelope.attrib:
        return envelope.attrib["srsName"]

    for element in root.iter():
        if "srsName" in element.attrib:
            return element.attrib["srsName"]

    return None
