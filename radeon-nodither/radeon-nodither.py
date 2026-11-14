#!/usr/bin/python3 -I
"""Turn off display dithering on Evergreen / Northern Islands Radeons (e.g. Turks, HD 6770M).

On the 2011 iMac the radeon driver dithers the built-in panel down to 6 bits,
which shows up as faint moving "snow" in dark greys. The panel and link are
really 8-bit, so the dithering can simply be switched off.

  radeon-nodither            show the dither state of each display pipe (read only)
  radeon-nodither off        clear dithering on active pipes, then re-check for
                             30 s in case the desktop resets the display mode
  radeon-nodither restore    put back the values saved by the last 'off'

Needs root. The setting lasts until the driver reprograms the display
(boot, resume from suspend), so the service runs it again at those points.
"""
import ctypes, glob, json, mmap, os, sys, time

SAVE = "/run/radeon-nodither.json"
CRTC_OFFSETS = [0x0, 0xc00, 0x9800, 0xa400, 0xb000, 0xbc00]   # display pipes 0-5
CRTC_CONTROL = 0x6e70             # bit 0: pipe enabled
FMT_BIT_DEPTH_CONTROL = 0x6fc8    # dithering / truncation settings
BITS = {
    "truncate": 1 << 0, "truncate_depth(8bit)": 1 << 4,
    "spatial_dither": 1 << 8, "spatial_depth(8bit)": 1 << 12,
    "frame_random": 1 << 13, "rgb_random": 1 << 14, "highpass_random": 1 << 15,
    "temporal_dither": 1 << 16, "temporal_depth(8bit)": 1 << 20, "temporal_level": 1 << 24,
}
DITHER_MASK = (1 << 8) | (1 << 13) | (1 << 14) | (1 << 15) | (1 << 16)
RECHECK = [2, 3, 5, 10, 10]       # seconds between re-checks after 'off' (30 s total)


def open_regs():
    devs = glob.glob("/sys/bus/pci/drivers/radeon/0000:*")
    if not devs:
        sys.exit("radeon-nodither: no device bound to the radeon driver")
    fd = os.open(devs[0] + "/resource2", os.O_RDWR | os.O_SYNC)   # register BAR
    mm = mmap.mmap(fd, 0x20000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    return lambda off: ctypes.c_uint32.from_buffer(mm, off)


def active_pipes(reg):
    for i, base in enumerate(CRTC_OFFSETS):
        yield i, bool(reg(base + CRTC_CONTROL).value & 1), base + FMT_BIT_DEPTH_CONTROL


def clear(reg, saved):
    for i, on, off in active_pipes(reg):
        r = reg(off)
        if on and r.value & DITHER_MASK:
            saved.setdefault(str(off), r.value)
            r.value &= ~DITHER_MASK
            print(f"pipe {i}: dithering cleared (was 0x{saved[str(off)]:08x})", flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "show"
    reg = open_regs()
    if mode == "show":
        for i, on, off in active_pipes(reg):
            v = reg(off).value
            flags = [n for n, b in BITS.items() if v & b] or ["none"]
            print(f"pipe {i}: {'ACTIVE' if on else 'off   '}  FMT=0x{v:08x}  {', '.join(flags)}")
    elif mode == "off":
        saved = {}
        clear(reg, saved)
        if saved:
            json.dump(saved, open(SAVE, "w"))
        for delay in RECHECK:
            time.sleep(delay)
            clear(reg, saved)
        if saved:
            json.dump(saved, open(SAVE, "w"))
    elif mode == "restore":
        for off, val in json.load(open(SAVE)).items():
            reg(int(off)).value = val
            print(f"restored 0x{int(off):05x} = 0x{val:08x}")
    else:
        sys.exit(__doc__)


main()
