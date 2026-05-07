import os

import numpy as np
import trimesh

# BUG FIX #1: Removed `import open3d as o3d` and `from scipy.spatial import cKDTree`.
# Both were imported but never used anywhere in this file — they were left over from
# a previous KD-Tree implementation that was replaced with the AABB centroid test.
# Keeping them forces two heavy optional dependencies (open3d, scipy) to be installed
# and imported even when only the partitioner is needed.


def should_spatially_partition(meta, bbox, mode="auto"):
    if isinstance(mode, bool):
        return mode
    if isinstance(mode, int):
        return bool(mode)
    if str(mode).lower() in {"false", "off", "none", "no", "0"}:
        return False
    if str(mode).lower() in {"true", "on", "yes", "1"}:
        return True

    # mode="auto": adaptive decision logic
    faces = int(meta.get("faces", 0))
    width = float(bbox.get("width", 0.0))
    depth = float(bbox.get("depth", 0.0))

    if width >= 200 or depth >= 200:
        return True
    if faces >= 150000:
        return True
    return False


class AdaptiveNode:
    """
    Represents a single leaf node of the octree partition.
    Vertices and faces are in global coordinate space; offset is subtracted
    at export time to maximize floating-point precision in the GLB.
    """
    def __init__(self, bounds, vertices, faces):
        self.bounds = bounds  # np.array([min_xyz, max_xyz])
        self.vertices = vertices
        self.faces = faces

        self.center = (bounds[0] + bounds[1]) / 2.0
        self.offset = bounds[0].astype(float)
        self.geometric_error = max(
            float(bounds[1][0] - bounds[0][0]),
            float(bounds[1][1] - bounds[0][1])
        )

    @property
    def face_count(self):
        return len(self.faces)

    def export(self, output_glb):
        """
        Export node geometry to GLB, shifted to local origin for precision.
        """
        if len(self.vertices) == 0 or len(self.faces) == 0:
            return

        local_vertices = self.vertices - self.offset

        scene = trimesh.Scene()
        mesh = trimesh.Trimesh(vertices=local_vertices, faces=self.faces, process=False)
        scene.add_geometry(mesh, node_name="node")

        out_dir = os.path.dirname(output_glb)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        scene.export(output_glb, file_type="glb")


class AdaptiveOctree:
    def __init__(self, full_mesh, max_depth=4, target_faces_per_cell=80000):
        """
        Recursively partitions a trimesh.Trimesh into spatial leaf nodes
        using octree subdivision. Each leaf is an AdaptiveNode.
        """
        self.mesh = full_mesh
        self.max_depth = max_depth
        self.target_faces_per_cell = target_faces_per_cell

        self.root_bounds = self.mesh.bounds
        self.mins = np.asarray(self.root_bounds[0])
        self.maxs = np.asarray(self.root_bounds[1])

        self.leaves = []

        # Precompute all triangle centroids once (global, for reference)
        self._all_centroids = np.asarray(self.mesh.triangles_center)

        all_face_indices = np.arange(len(self.mesh.faces))

        print(f"[Chunking] Octree partitioning {len(self.mesh.faces)} triangles")
        self._partition_recursive(
            current_face_indices=all_face_indices,
            depth=0,
            node_mins=self.mins,
            node_maxs=self.maxs,
        )

    @property
    def count(self):
        return len(self.leaves)

    def _partition_recursive(self, current_face_indices, depth, node_mins, node_maxs):
        """
        Recursively subdivide the set of face indices into 8 octants.

        Each call receives *indices into self.mesh.faces* (face indices), not face
        arrays, so centroid lookups via self._all_centroids[current_face_indices]
        are always correctly scoped to the global centroid array.
        """
        if len(current_face_indices) == 0:
            return

        # Adaptive subdivision decision
        should_split = (
            depth < self.max_depth
            and len(current_face_indices) > self.target_faces_per_cell
        )

        # BUG FIX #2: The original slenderness guard used `float(np.min(size))` which
        # returns 0.0 for any flat/thin mesh (e.g. roads, rooftops, ground planes),
        # making the ratio infinite or meaningless. The guard's intent is to stop
        # splitting extremely elongated bounding boxes — use the *second smallest*
        # dimension instead, so a flat-but-wide box (valid geometry) is not
        # incorrectly blocked from splitting.
        size = node_maxs - node_mins
        sorted_dims = np.sort(size)                         # ascending
        second_min = max(float(sorted_dims[1]), 0.001)      # ignore the thinnest axis
        if depth >= 2 and float(np.max(size)) / second_min > 10.0:
            should_split = False

        if not should_split:
            self._create_leaf(current_face_indices, node_mins, node_maxs)
            return

        # BUG FIX #3 + #4 + #5 (three related bugs, one root cause):
        #
        # BUG FIX #3 — Upper-boundary drop:
        #   The AABB test used strict `< octant_maxs`. Any centroid that sits exactly
        #   on node_maxs (valid for boundary-vertex geometry) fell through all 8
        #   octants and was silently discarded. This caused triangles at mesh edges
        #   to disappear from the output tileset.
        #
        # BUG FIX #4 — Zero-span axis drop:
        #   When a mesh is flat on one axis (roads, ground planes, rooftops),
        #   node_maxs[axis] == node_mins[axis], so center[axis] == node_maxs[axis].
        #   The upper half of that axis had an empty interval [center, node_maxs),
        #   and the lower half [node_mins, center) excluded the centroid that lay
        #   exactly on the boundary. Every centroid failed the AABB test on that axis,
        #   so ALL faces in the node were lost.
        #
        # BUG FIX #5 — Epsilon not propagated to children:
        #   Even if a one-off epsilon were applied at the current level, the original
        #   recursive calls passed the unmodified node_maxs down, re-introducing the
        #   boundary bug at every subsequent split level.
        #
        # Root fix: expand node_maxs by a floating-point epsilon on EVERY axis before
        # computing the center and the octant upper bounds. This makes the half-open
        # [min, max) intervals capture all centroids including those on the boundary,
        # and it turns zero-span axes into a tiny non-zero span so the test works.
        # Passing effective_maxs to child calls (instead of node_maxs) propagates
        # the fix correctly through the entire recursion tree.
        eps = np.maximum(np.abs(node_maxs), 1.0) * np.finfo(float).eps * 8
        effective_maxs = node_maxs + eps          # expand unconditionally on all axes
        center = (node_mins + effective_maxs) / 2.0

        dims = node_maxs - node_mins
        print(
            f"[Chunking] Slicing depth={depth}: "
            f"box={dims[0]:.1f}x{dims[1]:.1f}x{dims[2]:.1f}m, "
            f"faces={len(current_face_indices)}"
        )

        node_centroids = self._all_centroids[current_face_indices]

        for ix in [0, 1]:
            for iy in [0, 1]:
                for iz in [0, 1]:

                    octant_mins = np.array([
                        node_mins[0] if ix == 0 else center[0],
                        node_mins[1] if iy == 0 else center[1],
                        node_mins[2] if iz == 0 else center[2],
                    ])
                    # BUG FIX #3/#4: Use effective_maxs (not node_maxs) as the upper
                    # bound for the high-side octants. This ensures the strict < test
                    # captures centroids at the exact boundary.
                    octant_maxs = np.array([
                        center[0] if ix == 0 else effective_maxs[0],
                        center[1] if iy == 0 else effective_maxs[1],
                        center[2] if iz == 0 else effective_maxs[2],
                    ])

                    inside = (
                        (node_centroids >= octant_mins).all(axis=1)
                        & (node_centroids < octant_maxs).all(axis=1)
                    )
                    child_face_indices = current_face_indices[inside]

                    if len(child_face_indices) == 0:
                        continue

                    self._partition_recursive(
                        current_face_indices=child_face_indices,
                        depth=depth + 1,
                        node_mins=octant_mins,
                        # BUG FIX #5: Pass effective_maxs (not node_maxs) so the
                        # epsilon expansion propagates correctly to all child levels.
                        node_maxs=octant_maxs,
                    )

    def _create_leaf(self, current_face_indices, node_mins, node_maxs):
        if len(current_face_indices) == 0:
            return

        current_faces = np.asarray(self.mesh.faces)[current_face_indices]

        # Collect and remap vertices to local index space
        unique_indices = np.unique(current_faces.flatten())
        if len(unique_indices) == 0:
            return

        unique_vertices = np.asarray(self.mesh.vertices)[unique_indices]

        index_map = {old: new for new, old in enumerate(unique_indices)}
        remapped_faces = np.vectorize(index_map.get)(current_faces)

        leaf_node = AdaptiveNode(
            bounds=np.array([node_mins, node_maxs]),
            vertices=unique_vertices,
            faces=remapped_faces,
        )
        self.leaves.append(leaf_node)


def build_adaptive_partitioner(mesh, max_depth, target_faces_per_cell):
    """
    Entry point: validates the mesh and returns a populated AdaptiveOctree.
    The `scene` parameter was intentionally removed — the caller is responsible
    for converting a scene to a single mesh before calling this function.
    """
    if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        return None

    return AdaptiveOctree(
        full_mesh=mesh,
        max_depth=max_depth,
        target_faces_per_cell=target_faces_per_cell,
    )