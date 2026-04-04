import math


class BBox2D:
    def __init__(self, min_lon, min_lat, max_lon, max_lat):
        self.min_lon = min_lon
        self.min_lat = min_lat
        self.max_lon = max_lon
        self.max_lat = max_lat

    @property
    def width_deg(self):
        return self.max_lon - self.min_lon

    @property
    def height_deg(self):
        return self.max_lat - self.min_lat

    @property
    def center_lon(self):
        return (self.min_lon + self.max_lon) / 2.0

    @property
    def center_lat(self):
        return (self.min_lat + self.max_lat) / 2.0

    def contains(self, lon, lat):
        return (
            self.min_lon <= lon <= self.max_lon
            and self.min_lat <= lat <= self.max_lat
        )

    def subdivide(self):
        cx = self.center_lon
        cy = self.center_lat
        return [
            BBox2D(self.min_lon, cy, cx, self.max_lat),
            BBox2D(cx, cy, self.max_lon, self.max_lat),
            BBox2D(self.min_lon, self.min_lat, cx, cy),
            BBox2D(cx, self.min_lat, self.max_lon, cy),
        ]

    def to_region(self, min_height=0.0, max_height=300.0):
        return {
            "region": [
                math.radians(self.min_lon),
                math.radians(self.min_lat),
                math.radians(self.max_lon),
                math.radians(self.max_lat),
                min_height,
                max_height,
            ]
        }


class QuadNode:
    def __init__(self, bounds, depth=0, max_depth=4, max_per_cell=4):
        self.bounds = bounds
        self.depth = depth
        self.max_depth = max_depth
        self.max_per_cell = max_per_cell
        self.models = []
        self.children = []

    @property
    def is_leaf(self):
        return not self.children

    def insert(self, model):
        lon = float(model["lon"])
        lat = float(model["lat"])

        if not self.bounds.contains(lon, lat):
            return False

        if self.is_leaf:
            self.models.append(model)
            if len(self.models) > self.max_per_cell and self.depth < self.max_depth:
                self._split()
            return True

        for child in self.children:
            if child.insert(model):
                return True

        self.models.append(model)
        return True

    def _split(self):
        self.children = [
            QuadNode(
                bounds=child_bounds,
                depth=self.depth + 1,
                max_depth=self.max_depth,
                max_per_cell=self.max_per_cell,
            )
            for child_bounds in self.bounds.subdivide()
        ]

        current = self.models
        self.models = []
        for model in current:
            placed = False
            for child in self.children:
                if child.insert(model):
                    placed = True
                    break
            if not placed:
                self.models.append(model)

    def leaves(self):
        if self.is_leaf:
            return [self] if self.models else []

        result = []
        for child in self.children:
            result.extend(child.leaves())
        if self.models:
            result.append(self)
        return result


def build_quadtree(models, padding_deg=0.005, max_depth=4, max_per_cell=4):
    if not models:
        return None

    lons = [float(model["lon"]) for model in models]
    lats = [float(model["lat"]) for model in models]

    span = max(max(lons) - min(lons), max(lats) - min(lats), 0.01)
    center_lon = (min(lons) + max(lons)) / 2.0
    center_lat = (min(lats) + max(lats)) / 2.0
    half = span / 2.0 + padding_deg

    root_bounds = BBox2D(
        center_lon - half,
        center_lat - half,
        center_lon + half,
        center_lat + half,
    )
    root = QuadNode(root_bounds, max_depth=max_depth, max_per_cell=max_per_cell)

    for model in models:
        root.insert(model)

    return root
