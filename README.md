# imac-2011-linux

Notes and fixes for running Linux on a mid-2011 27" iMac (**iMac12,2**). Tested on Fedora 45, kernel 7.3, GNOME on Wayland.

| Problem | Fix |
|---|---|
| HDD fan runs loud after replacing the Apple hard drive with an SSD | [imac-hddfan](https://github.com/zimchaa/imac-hddfan) |
| Faint moving "snow" on dark greys that isn't there in macOS | [`radeon-nodither/`](radeon-nodither/) |
| Windows games in Steam crash at launch (no Vulkan) | [`proton-wined3d/`](proton-wined3d/) |
| Startup chime ("bong") at every power-on | [`startup-chime/`](startup-chime/) |

## Fresh install checklist

After reinstalling Linux, clone this repo and run:

```
git clone https://github.com/zimchaa/imac-2011-linux.git
cd imac-2011-linux

sudo ./radeon-nodither/install.sh     # remove display snow
./startup-chime/chime.sh              # check the chime; usually still muted (stored in firmware)
sudo ./startup-chime/chime.sh mute    # only if it says "on"
./proton-wined3d/install.sh           # as your user, after Steam has installed Proton
```

Then install [imac-hddfan](https://github.com/zimchaa/imac-hddfan) if the hard drive has been replaced with an SSD.

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

## Steam games: Proton crashes at launch

**Symptom:** a Windows game started through Steam (Proton) closes after a second or two. The Unity game Cult of the Lamb, for example, logs `d3d11: failed to create factory (80004005)` in its `Player.log` and then crashes.

**Cause:** Proton runs Direct3D 9/10/11 through DXVK, which translates it into Vulkan. As explained above, this iMac has no usable Vulkan driver, so DXVK can't start.

**Fix:** set `PROTON_USE_WINED3D=1`, which makes Proton use WineD3D instead. WineD3D translates Direct3D into OpenGL, which the Radeon supports. [`proton-wined3d/`](proton-wined3d/) does this for every game through Proton's `user_settings.py` file, so nothing has to be set per game. That matters here because Steam's game Properties window may not open at all on this machine.

```
./proton-wined3d/install.sh      # as your user, not sudo
```

- It copies `user_settings.py` into every Proton version Steam has installed, including custom builds in `compatibilitytools.d`. It won't overwrite a different `user_settings.py` that's already there.
- Run it again after Steam installs a new Proton version, or after switching a game to one. Updates to an existing Proton version keep the file.
- It also turns on Proton logging, which writes `~/steam-<appid>.log` for each game. Delete that line from `user_settings.py` if you don't want the logs.
- For a single game, the launch option `PROTON_USE_WINED3D=1 %command%` does the same thing. For Unity games, `%command% -force-glcore` makes the game use OpenGL itself and is worth trying if WineD3D misbehaves.
- Games that need Direct3D 12 still won't run: WineD3D doesn't support it, and the alternative, vkd3d-proton, needs Vulkan too.
- Cult of the Lamb starts with this fix but doesn't run perfectly. Expect old-GPU performance with 512 MB of VRAM.

## Startup chime

**Symptom:** the Mac startup chime plays at full volume on every power-on and restart, with no macOS Sound settings to turn it off.

**Fix:** the chime volume is stored in the firmware's NVRAM as the EFI variable `SystemAudioVolume`. Setting its value to `0x80` (the mute bit) silences the chime. Linux can write EFI variables through `/sys/firmware/efi/efivars`. [`startup-chime/chime.sh`](startup-chime/chime.sh) does this:

```
./startup-chime/chime.sh          # show the current setting
sudo ./startup-chime/chime.sh mute
```

- The setting lives in firmware, not on the disk, so it survives reinstalling Linux.
- A PRAM/NVRAM reset (hold Cmd+Option+P+R at power-on) clears it, which also brings the chime back if you want it.
- Linux marks EFI variable files immutable to protect them, so the script removes that flag (`chattr -i`), writes the variable, then sets the flag again.
