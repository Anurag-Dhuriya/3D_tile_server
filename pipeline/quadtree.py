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
        # Half-open interval [min, max) on both axes.
        # This guarantees each point falls into exactly one child quadrant after
        # subdivide(), eliminating boundary overlap (Bug 1).
        # The root node uses contains_closed() to keep edge models inside the tree.
        return (
            self.min_lon <= lon < self.max_lon
            and self.min_lat <= lat < self.max_lat
        )

    def contains_closed(self, lon, lat):
        """Fully closed [min, max] check — used only for the root bounds."""
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

    def to_region(self, min_height=0.0, max_height=5000.0):
        """
        Returns a 3D Tiles region bounding volume.
        max_height defaults to 5000m — pass the actual scene height where known
        to avoid over- or under-culling.
        """
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

        # Use closed bounds at root (depth 0) so edge models aren't rejected;
        # child nodes use half-open bounds to avoid quadrant boundary overlaps.
        check = self.bounds.contains_closed if self.depth == 0 else self.bounds.contains
        if not check(lon, lat):
            return False

        if self.is_leaf:
            self.models.append(model)
            if len(self.models) > self.max_per_cell and self.depth < self.max_depth:
                self._split()
            return True

        for child in self.children:
            if child.insert(model):
                return True

        # Safety fallback: model fell through all children (should not happen after Bug 1 fix).
        print(f"[Quadtree] WARNING: model '{model.get('name', '?')}' at ({lon},{lat}) "
              f"not accepted by any child at depth {self.depth} — kept on parent node")
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

    lons = []
    lats = []
    for model in models:
        if "lon" not in model or "lat" not in model:
            raise ValueError(
                f"Model '{model.get('name', '?')}' is missing 'lon' or 'lat' — "
                f"cannot insert into quadtree"
            )
        lons.append(float(model["lon"]))
        lats.append(float(model["lat"]))

    center_lon = (min(lons) + max(lons)) / 2.0
    center_lat = (min(lats) + max(lats)) / 2.0

    # Use separate half-spans for lon and lat: they cover different physical
    # distances, especially at high latitudes (Bug 3).
    half_lon = max(max(lons) - min(lons), 0.01) / 2.0 + padding_deg
    half_lat = max(max(lats) - min(lats), 0.01) / 2.0 + padding_deg

    root_bounds = BBox2D(
        center_lon - half_lon,
        center_lat - half_lat,
        center_lon + half_lon,
        center_lat + half_lat,
    )
    root = QuadNode(root_bounds, max_depth=max_depth, max_per_cell=max_per_cell)

    for model in models:
        root.insert(model)

    return root