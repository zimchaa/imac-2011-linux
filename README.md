# imac-2011-linux

Notes and fixes for running Linux on a mid-2011 27" iMac (**iMac12,2**). Tested on Fedora 45, kernel 7.3, GNOME on Wayland.

| Problem | Fix |
|---|---|
| HDD fan runs loud after replacing the Apple hard drive with an SSD | [imac-hddfan](https://github.com/zimchaa/imac-hddfan) |
| Faint moving "snow" on dark greys that isn't there in macOS | [`radeon-nodither/`](radeon-nodither/) |

## Graphics: what this iMac has

The iMac12,2 has two GPUs, but only one drives the screen.

- **AMD Radeon HD 6770M** ("Turks", TeraScale 2, 512 MB), PCI `01:00.0`, `radeon` driver. The built-in 2560×1440 panel is wired to this GPU over internal DisplayPort (eDP).
- **Intel HD 3000** (Sandy Bridge), PCI `00:02.0`, `i915` driver. It isn't connected to the panel, so the desktop can't be moved onto it. It's only useful for video encoding.

What that means in practice:

- **OpenGL:** Mesa reports **OpenGL 4.6** core and compatibility profiles on the Radeon, so games and apps that need OpenGL 3.3 or newer will start. The GPU is from 2011 with 512 MB of VRAM, so expect to need low settings.
- **Vulkan:** neither GPU supports it. Mesa's AMD Vulkan driver (RADV) needs GCN or newer, and its Intel drivers start at Ivy Bridge. The only Vulkan available is the CPU-based `lavapipe`, which is far too slow for games. Pick OpenGL whenever there's a choice.
- **`amdgpu` driver:** doesn't support TeraScale cards, so this machine stays on `radeon`.
- **Phantom VGA output:** the Radeon reports a connected 1024×768 `VGA-2` display that doesn't exist. If the mouse disappears off one edge of the screen, turn it off in Settings → Displays.

## Display "snow" (dithering)

**Symptom:** a very faint, shimmering grain like a badly tuned analogue TV, most visible on dark grey (terminals, the login screen). It doesn't appear on pure black or in macOS.

**Cause:** the `radeon` driver lets the GPU's firmware (ATOM BIOS) set up the built-in panel. Apple's firmware doesn't describe the panel the way a PC video BIOS does, so the display pipe gets set to **spatial dithering down to 6 bits per colour, re-randomised every frame**. The panel and link are actually 8-bit, so the dithering adds grain and fixes nothing. Pure black isn't affected because there's no darker shade to dither towards.

The register (`FMT_BIT_DEPTH_CONTROL`, pipe 0) reads `0x0000a100` by default: spatial dither, 6-bit depth, frame random and high-pass random.

**What doesn't work:**
- `radeon.deep_color=1`: it applies (`/sys/module/radeon/parameters/deep_color` reads 1), but the snow remains, because dithering on the built-in panel is set by the firmware, not the driver's colour-depth logic.
- The driver's `dither` connector property: it isn't used for the built-in panel, and it can't be changed from inside a Wayland session anyway.
- A shell script: the register has to be written through the PCI register BAR. Fedora's kernel blocks `/dev/mem`-based tools such as `devmem2` for memory a driver has claimed, so bash can't do it alone.

**Fix:** [`radeon-nodither/`](radeon-nodither/) clears the dithering bits directly. With them cleared on this iMac, the snow is gone and gradients stay smooth, which shows the link really carries 8 bits. The driver sets the bits again when it reprograms the display, which happens at boot and **after resume from suspend**. So the service runs once at boot and once after each resume, re-checks for 30 seconds in case the desktop does its own mode set, then exits. Nothing keeps running.

```
cd radeon-nodither
sudo ./install.sh
sudo /usr/local/sbin/radeon-nodither        # pipe 0 should show: none
```

`snow-test.html` shows grey bands from 0 to 64. Open it in a browser to see the effect before and after.

| Command | What it does |
|---|---|
| `sudo /usr/local/sbin/radeon-nodither` | Show each pipe's dither state (read only) |
| `sudo /usr/local/sbin/radeon-nodither off` | Clear dithering now |
| `sudo /usr/local/sbin/radeon-nodither restore` | Put back the values saved by the last `off` |
| `sudo ./uninstall.sh` | Remove the service and restore dithering |

Notes:
- It writes GPU registers directly. The register layout comes from the Linux `radeon` driver's Evergreen definitions, which cover the HD 5000/6000 generation. It has only been tested on the iMac12,2's HD 6770M. On other machines, run the read-only command first and check that the output makes sense: one or two `ACTIVE` pipes, the rest `off`.
- It needs a kernel that isn't in lockdown mode. If `/sys/kernel/security/lockdown` shows `[integrity]` or `[confidentiality]`, which some Secure Boot setups enable, the register file can't be opened.
- `/usr/local/sbin` isn't on `sudo`'s path in Fedora, so use the full path.
