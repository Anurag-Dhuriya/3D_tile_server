import glob
import os
import shutil
import subprocess


CITYDB_SUPPORTED_IMPORTS = {
    ".gml": "citygml",
    ".xml": "citygml",
    ".cityjson": "cityjson",
    ".json": "cityjson",
}


def _emit_stage(stage_hook, stage, detail=None):
    if stage_hook is None:
        return
    try:
        stage_hook(stage, detail)
    except Exception:
        pass


def should_use_citydb_backend(model, source_path=None, settings=None):
    if settings is None:
        return False

    backend = str(settings.get("input_backend", "direct")).lower()
    if backend != "3dcitydb":
        return False

    if source_path:
        ext = os.path.splitext(source_path)[1].lower()
        return ext in CITYDB_SUPPORTED_IMPORTS

    return True


def ensure_citydb_available(settings):
    tool = settings.get("citydb_tool_path") or shutil.which("citydb")
    if not tool:
        raise RuntimeError(
            "3DCityDB backend requested but citydb-tool was not found. "
            "Set pipeline.citydb_tool_path or install citydb-tool."
        )

    if not os.path.isfile(tool):
        raise RuntimeError(f"citydb-tool not found: {tool}")

    return tool


def detect_citydb_import_format(source_path):
    ext = os.path.splitext(source_path)[1].lower()
    fmt = CITYDB_SUPPORTED_IMPORTS.get(ext)
    if not fmt:
        raise RuntimeError(f"Unsupported 3DCityDB import format: {ext}")
    return fmt


def _append_connection_options(cmd, settings):
    if settings.get("citydb_db_host"):
        cmd.append(f"--db-host={settings['citydb_db_host']}")
    if settings.get("citydb_db_port"):
        cmd.append(f"--db-port={settings['citydb_db_port']}")
    if settings.get("citydb_db_name"):
        cmd.append(f"--db-name={settings['citydb_db_name']}")
    if settings.get("citydb_db_schema"):
        cmd.append(f"--db-schema={settings['citydb_db_schema']}")
    if settings.get("citydb_db_username"):
        cmd.append(f"--db-username={settings['citydb_db_username']}")
    if settings.get("citydb_db_password"):
        cmd.append(f"--db-password={settings['citydb_db_password']}")
    return cmd


def _base_citydb_command(settings):
    tool = ensure_citydb_available(settings)
    cmd = [tool]

    config_file = settings.get("citydb_config_file")
    if config_file:
        cmd.append(f"--config-file={config_file}")

    return _append_connection_options(cmd, settings)


def _run_citydb_command(cmd, label):
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        message = stderr or stdout or f"{label} failed"
        raise RuntimeError(message)

    return result


def import_city_model_to_db(source_path, dataset_name, settings, stage_hook=None):
    """
    MVP assumption:
    one dataset per dedicated DB/schema.
    This import step loads the full CityGML/CityJSON source into that target.
    """
    _emit_stage(stage_hook, "citydb_import", dataset_name)

    source_format = detect_citydb_import_format(source_path)
    cmd = _base_citydb_command(settings)

    cmd.extend([
        "import",
        source_format,
        source_path,
    ])

    threads = int(settings.get("citydb_threads", 4))
    if threads > 0:
        cmd.append(f"--threads={threads}")

    index_mode = settings.get("citydb_index_mode", "keep")
    if index_mode:
        cmd.append(f"--index-mode={index_mode}")

    if settings.get("citydb_compute_extent", True):
        cmd.append("--compute-extent")

    import_mode = settings.get("citydb_import_mode", "import_all")
    if import_mode:
        cmd.append(f"--import-mode={import_mode}")

    if source_format == "citygml" and settings.get("citydb_no_appearances", False):
        cmd.append("--no-appearances")

    _run_citydb_command(cmd, "3DCityDB import")
    return {
        "dataset_name": dataset_name,
        "source_format": source_format,
    }


def export_citydb_tiles(dataset_name, output_dir, settings, stage_hook=None):
    """
    MVP assumption:
    the configured DB/schema contains only the dataset we want to export.
    The export command writes tiled CityGML files, which are then fed into the
    existing local CityGML processing pipeline.
    """
    export_format = str(settings.get("citydb_export_format", "citygml")).lower()
    if export_format != "citygml":
        raise RuntimeError(
            "This first 3DCityDB integration pass only supports "
            "pipeline.citydb_export_format='citygml'."
        )

    _emit_stage(stage_hook, "citydb_export", dataset_name)

    shutil.rmtree(output_dir, ignore_errors=True)
    os.makedirs(output_dir, exist_ok=True)

    output_pattern = os.path.join(output_dir, "@column@", "tile_@row@.gml")
    cmd = _base_citydb_command(settings)

    cmd.extend([
        "export",
        "citygml",
        f"--output={output_pattern}",
    ])

    threads = int(settings.get("citydb_threads", 4))
    if threads > 0:
        cmd.append(f"--threads={threads}")

    tile_dimension_m = float(settings.get("citydb_tile_dimension_m", 500.0))
    cmd.append(f"--tile-dimension={tile_dimension_m},{tile_dimension_m}")

    tile_origin = settings.get("citydb_tile_origin", "top_left")
    if tile_origin:
        cmd.append(f"--tile-origin={tile_origin}")

    citygml_version = settings.get("citydb_citygml_version", "3.0")
    if citygml_version:
        cmd.append(f"--citygml-version={citygml_version}")

    export_srid = settings.get("citydb_export_srid")
    if export_srid:
        cmd.append(f"--crs={export_srid}")

    if settings.get("citydb_fail_fast", True):
        cmd.append("--fail-fast")

    _run_citydb_command(cmd, "3DCityDB tiled export")

    exported_tiles = sorted(
        glob.glob(os.path.join(output_dir, "**", "*.gml"), recursive=True)
    )

    max_tiles = int(settings.get("citydb_max_export_tiles", 256))
    if len(exported_tiles) > max_tiles:
        raise RuntimeError(
            f"3DCityDB export produced {len(exported_tiles)} tiles, "
            f"which exceeds citydb_max_export_tiles={max_tiles}. "
            f"Increase tile size or raise the limit."
        )

    if not exported_tiles:
        raise RuntimeError("3DCityDB export produced no CityGML tiles")

    return exported_tiles
