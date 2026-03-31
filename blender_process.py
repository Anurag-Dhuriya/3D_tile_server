import bpy
import sys
import os


def clean_and_export(input_path, output_path, scale_unit):

    print(f"[Blender] Blender version : {bpy.app.version_string}")
    print(f"[Blender] Input           : {input_path}")
    print(f"[Blender] Output          : {output_path}")
    print(f"[Blender] Unit            : {scale_unit}")

    # Validate input file exists
    if not os.path.isfile(input_path):
        print(f"[Blender] ERROR: Input file not found: {input_path}")
        sys.exit(1)

    # Clear default scene
    print(f"[Blender] Clearing scene...")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    # Import OBJ — Blender 4.x+ / 5.x API
    print(f"[Blender] Importing OBJ...")
    try:
        bpy.ops.wm.obj_import(
            filepath=input_path,
            forward_axis='NEGATIVE_Z',
            up_axis='Y'
        )
        print(f"[Blender] Import successful")
    except Exception as e:
        print(f"[Blender] ERROR importing OBJ: {e}")
        sys.exit(1)

    # Check objects were imported
    objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    print(f"[Blender] Mesh objects found: {len(objects)}")

    if len(objects) == 0:
        print(f"[Blender] ERROR: No mesh objects found after import")
        sys.exit(1)

    # Select all mesh objects
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]

    # Join into one object if multiple
    if len(objects) > 1:
        print(f"[Blender] Joining {len(objects)} objects into one...")
        bpy.ops.object.join()

    obj = bpy.context.active_object
    print(f"[Blender] Active object: {obj.name}")

    # Apply scale correction based on source unit
    scale_factors = {
        'mm':  0.001,
        'cm':  0.01,
        'ft':  0.3048,
        'in':  0.0254,
        'm':   1.0
    }
    scale = scale_factors.get(scale_unit, 1.0)

    if scale != 1.0:
        print(f"[Blender] Scaling: {scale_unit} → meters (factor: {scale})")
        obj.scale = (scale, scale, scale)

    # Apply all transforms
    print(f"[Blender] Applying transforms...")
    bpy.ops.object.transform_apply(
        location=True,
        rotation=True,
        scale=True
    )

    # Geometry cleanup in edit mode
    print(f"[Blender] Cleaning geometry...")
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Apply transforms again after cleanup
    bpy.ops.object.transform_apply(
        location=True,
        rotation=True,
        scale=True
    )

    # Print model stats
    mesh = obj.data
    print(f"[Blender] Vertices : {len(mesh.vertices)}")
    print(f"[Blender] Polygons : {len(mesh.polygons)}")

    # Calculate bounding box dimensions
    bbox         = obj.bound_box
    xs           = [v[0] for v in bbox]
    ys           = [v[1] for v in bbox]
    zs           = [v[2] for v in bbox]
    width        = max(xs) - min(xs)
    depth        = max(ys) - min(ys)
    model_height = max(zs) - min(zs)

    print(f"[Blender] Width  : {width:.2f}m")
    print(f"[Blender] Depth  : {depth:.2f}m")
    print(f"[Blender] Height : {model_height:.2f}m")

    # Write bounding box so server.py can use actual dimensions
    bbox_path = output_path.replace('.glb', '_bbox.txt')
    with open(bbox_path, 'w') as f:
        f.write(f"{width},{depth},{model_height}")
    print(f"[Blender] Bounding box written → {bbox_path}")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Export GLB — confirmed working on Blender 5.x
    print(f"[Blender] Exporting GLB → {output_path}")
    try:
        bpy.ops.export_scene.gltf(
            filepath=output_path,
            export_format='GLB',
            export_apply=True,
            export_normals=True,
            export_texcoords=True,
            export_materials='EXPORT',
            export_cameras=False,
            export_lights=False,
            use_selection=False
        )
    except Exception as e:
        print(f"[Blender] ERROR exporting GLB: {e}")
        sys.exit(1)

    # Verify file was created
    if not os.path.isfile(output_path):
        print(f"[Blender] ERROR: GLB not found at {output_path} after export")
        sys.exit(1)

    size = os.path.getsize(output_path)
    print(f"[Blender] GLB created — {size/1024:.1f} KB")
    print(f"[Blender] Done")


# Parse arguments passed after --
argv = sys.argv
try:
    idx         = argv.index('--')
    input_path  = argv[idx + 1]
    output_path = argv[idx + 2]
    scale_unit  = argv[idx + 3] if len(argv) > idx + 3 else 'm'
except (ValueError, IndexError):
    print("[Blender] ERROR: Missing arguments")
    print("[Blender] Usage: blender --background --python blender_process.py -- input.obj output.glb [unit]")
    sys.exit(1)

clean_and_export(input_path, output_path, scale_unit)