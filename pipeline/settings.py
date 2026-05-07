import copy


DEFAULT_PIPELINE_SETTINGS = {
    "spatial_chunking": "auto",
    "chunk_size_m": 5000.0,
    "max_chunks": 512,
    "max_octree_depth": 4,
    "faces_per_cell": 80000,
}


def pipeline_settings_for_model(model):
    settings = copy.deepcopy(DEFAULT_PIPELINE_SETTINGS)

    if not isinstance(model, dict):
        return settings

    for key in DEFAULT_PIPELINE_SETTINGS:
        if key in model:
            settings[key] = model[key]

    overrides = model.get("pipeline", {})
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in settings:
                settings[key] = value

    return settings
