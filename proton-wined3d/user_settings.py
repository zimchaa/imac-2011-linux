# iMac12,2 (Radeon HD 6770M, r600 driver): there's no Vulkan driver, so Proton's
# default D3D-to-Vulkan layer (DXVK) can't start and D3D10/11 games crash at launch.
# Proton loads this file from its own folder and applies it to every game it runs.
user_settings = {
    # Use OpenGL-based wined3d for d3d9/10/11 instead of Vulkan-based DXVK
    "PROTON_USE_WINED3D": "1",
    # Write ~/steam-<appid>.log for troubleshooting; delete this line to turn it off
    "PROTON_LOG": "1",
}
