import copy
import shutil


DEFAULT_PIPELINE_SETTINGS = {
    "input_backend": "direct",

    "spatial_chunking": "auto",
    "chunk_size_m": 5000.0,
    "max_chunks": 512,
    "max_octree_depth": 4,
    "faces_per_cell": 80000,

    "citydb_tool_path": shutil.which("citydb"),
    "citydb_config_file": None,

    "citydb_db_host": None,
    "citydb_db_port": 5432,
    "citydb_db_name": None,
    "citydb_db_schema": None,
    "citydb_db_username": None,
    "citydb_db_password": None,

    "citydb_threads": 4,
    "citydb_index_mode": "keep",
    "citydb_compute_extent": True,
    "citydb_import_mode": "import_all",
    "citydb_no_appearances": False,

    "citydb_export_format": "citygml",
    "citydb_citygml_version": "3.0",
    "citydb_tile_dimension_m": 500.0,
    "citydb_tile_origin": "top_left",
    "citydb_export_srid": None,
    "citydb_fail_fast": True,
    "citydb_max_export_tiles": 256,
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
