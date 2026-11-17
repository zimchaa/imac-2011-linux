#!/usr/bin/env python3
"""Record the screen or a window with H.264 encoding on the Intel HD 3000.

No root needed: frames come from GNOME through the xdg-desktop-portal
ScreenCast API (the same "share your screen" dialog browsers use) and
PipeWire. The mouse pointer is included, and a single window can be picked.

    GStreamer (this process)                      ffmpeg (child process)
    pipewiresrc      frames from GNOME            rawvideo on a pipe
    -> vapostproc    Radeon: scale to fit,        -> hwupload     to the Intel GPU
                     BGRx -> RGBx, into RAM       -> scale_vaapi  Intel: RGBx -> NV12
    -> videorate     steady 30 fps                -> h264_vaapi   Intel: H.264
    -> fdsink  ---------- pipe ---------->        (+ audio from PulseAudio) -> .mp4

Why the split: GStreamer can talk to the portal/PipeWire, but its VA H.264
encoder is broken with the i965 driver on Sandy Bridge (every P-frame is
nearly as big as a keyframe: ~20x bigger files). ffmpeg's encoder works.
Why the Radeon scales: the Intel GPU only takes up to 2048x2048. Why it stays
RGB: the Radeon's RGB -> NV12 conversion gets the colours wrong.

usage: ./portal-record.py [options]   (stop with Ctrl+C, or GNOME's stop button)
"""
import argparse
import datetime
import fcntl
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

try:  # GLib >= 2.80 moved unix_signal_add; fall back for older versions
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix
    signal_add = GLibUnix.signal_add
except (ImportError, ValueError):
    signal_add = GLib.unix_signal_add

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
INTEL_MAX = 2048  # Sandy Bridge encoder limit, both directions
F_SETPIPE_SZ = 1031


def log(msg):
    print(f"portal-record: {msg}", file=sys.stderr, flush=True)


def die(msg):
    log(msg)
    sys.exit(1)


# --- finding the GPUs -------------------------------------------------------
def find_va_element(kind, gpu_words):
    """GStreamer's VA plugin registers elements per GPU: the first GPU gets
    plain names (vapostproc), others get the render node in the name
    (varenderD129postproc), and the order can change. So pick the element
    by the GPU name in its description instead."""
    for f in Gst.Registry.get().get_feature_list(Gst.ElementFactory):
        name = f.get_name()
        if name.startswith("va") and name.endswith(kind):
            if any(w in (f.get_metadata("long-name") or "") for w in gpu_words):
                return name
    return None


def find_render_node(driver):
    """/dev/dri/renderD* of the GPU using this kernel driver (e.g. i915)."""
    for n in sorted(Path("/sys/class/drm").glob("renderD*")):
        if (n / "device/driver").resolve().name == driver:
            return f"/dev/dri/{n.name}"
    return None


# --- the portal handshake ---------------------------------------------------
# Each portal call returns a Request object path; the real answer arrives as
# its Response signal. call() hides that: it waits for the signal and returns
# the results dict (or exits if the user cancelled).
class Portal:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.request_prefix = f"/org/freedesktop/portal/desktop/request/{sender}/"
        self.counter = 0
        self.session = None

    def _token(self):
        self.counter += 1
        return f"portalrecord{os.getpid()}_{self.counter}"

    def call(self, method, args_fmt, args, options):
        token = self._token()
        options["handle_token"] = GLib.Variant("s", token)
        loop = GLib.MainLoop()
        result = {}

        def on_response(_bus, _sender, _path, _iface, _signal, params):
            result["code"], result["results"] = params.unpack()
            loop.quit()

        # Subscribe before calling, so the answer can't arrive unheard.
        sub = self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Request", "Response",
            self.request_prefix + token, None, Gio.DBusSignalFlags.NO_MATCH_RULE,
            on_response)
        self.bus.call_sync(
            PORTAL, PORTAL_PATH, SCREENCAST, method,
            GLib.Variant(f"({args_fmt}a{{sv}})", (*args, options)),
            None, Gio.DBusCallFlags.NONE, -1, None)
        loop.run()
        self.bus.signal_unsubscribe(sub)
        if result["code"] == 1:
            die("cancelled")
        if result["code"] != 0:
            die(f"portal {method} failed (code {result['code']})")
        return result["results"]

    def get_property(self, prop):
        v = self.bus.call_sync(
            PORTAL, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (SCREENCAST, prop)),
            None, Gio.DBusCallFlags.NONE, -1, None)
        return v.unpack()[0]

    def start(self, source_types):
        """Show GNOME's picker; return (pipewire fd, node id, (w, h))."""
        res = self.call("CreateSession", "", (),
                        {"session_handle_token": GLib.Variant("s", self._token())})
        self.session = res["session_handle"]

        # Cursor mode 2 = drawn into the frames. Fall back to hidden (1).
        cursor = 2 if self.get_property("AvailableCursorModes") & 2 else 1
        self.call("SelectSources", "o", (self.session,), {
            "types": GLib.Variant("u", source_types),   # 1 monitor, 2 window
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", cursor),
        })
        res = self.call("Start", "os", (self.session, ""), {})
        node, props = res["streams"][0]
        size = props.get("size", (0, 0))

        # A file descriptor that lets PipeWire show us only the chosen stream.
        reply, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})),
            None, Gio.DBusCallFlags.NONE, -1, None, None)
        return fds.get(reply.unpack()[0]), node, size

    def on_closed(self, callback):
        """Call callback when GNOME ends the session (its stop button)."""
        self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Session", "Closed", self.session,
            None, Gio.DBusSignalFlags.NONE, lambda *a: callback())


# --- sizes ------------------------------------------------------------------
def fit(src_w, src_h, max_w, max_h):
    """Scale (src_w, src_h) down to fit max_w x max_h, keeping the shape.
    Never scales up. H.264 needs even sizes, so round down to even."""
    scale = min(1.0, max_w / src_w, max_h / src_h)
    return int(src_w * scale) // 2 * 2, int(src_h * scale) // 2 * 2


def parse_size(s):
    try:
        w, h = (int(x) for x in s.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 1920x1080")
    if not (16 <= w <= INTEL_MAX and 16 <= h <= INTEL_MAX):
        raise argparse.ArgumentTypeError(
            f"the Intel HD 3000 can't encode larger than {INTEL_MAX}x{INTEL_MAX}")
    return w, h


# --- the two halves ---------------------------------------------------------
def gst_pipeline(radeon_pp, fd, node, w, h, fps, out_fd):
    return (
        # keepalive-time resends the last frame when the screen is idle, so
        # videorate keeps producing frames.
        f"pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=1000 "
        f"! queue "
        # Radeon video processor: scale and swap to RGBx, then copy into RAM.
        # add-borders keeps the shape if a recorded window is resized.
        f"! {radeon_pp} add-borders=true "
        f"! video/x-raw,format=RGBx,width={w},height={h},pixel-aspect-ratio=1/1 "
        # GNOME only sends frames when something changes; ffmpeg's rawvideo
        # input assumes a constant rate, so duplicate/drop to exactly fps.
        f"! videorate ! video/x-raw,framerate={fps}/1 "
        f"! fdsink fd={out_fd} sync=false"
    )


def parse_rate(s):
    """'6M', '4500k' or '6000000' -> bits per second."""
    try:
        mult = {"k": 1e3, "m": 1e6}.get(s[-1].lower(), 1)
        bps = int(float(s[:-1] if mult != 1 else s) * mult)
    except (ValueError, IndexError):
        raise argparse.ArgumentTypeError("bitrate must look like 6M or 4500k")
    if bps < 100_000:
        raise argparse.ArgumentTypeError("bitrate is too low (use e.g. 6M)")
    return bps


def ffmpeg_command(intel_node, w, h, fps, qp, bitrate, audio, out):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats", "-y",
           "-init_hw_device", f"vaapi=intel:{intel_node}", "-filter_hw_device", "intel",
           # video: raw frames from GStreamer on stdin
           "-f", "rawvideo", "-pix_fmt", "rgb0", "-s", f"{w}x{h}", "-framerate", str(fps),
           "-thread_queue_size", "64", "-i", "pipe:0"]
    # audio: PulseAudio (PipeWire's pulse server). @DEFAULT_MONITOR@ is
    # whatever is playing through the speakers.
    sources = {"none": [], "desktop": ["@DEFAULT_MONITOR@"], "mic": ["default"],
               "both": ["@DEFAULT_MONITOR@", "default"]}[audio]
    for s in sources:
        cmd += ["-f", "pulse", "-thread_queue_size", "1024", "-i", s]
    cmd += ["-map", "0:v",
            # upload to the Intel GPU, convert to NV12 there, encode there
            "-vf", "hwupload,scale_vaapi=format=nv12",
            "-c:v", "h264_vaapi"]
    if bitrate:
        # variable bitrate averaging `bitrate`, peaks up to a third higher
        cmd += ["-b:v", str(bitrate), "-maxrate", str(bitrate * 4 // 3)]
    else:
        # constant quality: same picture quality throughout, size varies
        cmd += ["-qp", str(qp)]
    if len(sources) == 1:
        cmd += ["-map", "1:a"]
    elif len(sources) == 2:
        cmd += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest[a]",
                "-map", "[a]"]
    if sources:
        # -shortest: stop when the video stops, not when audio does (never)
        cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def main():
    default_out = Path.home() / "Videos" / datetime.datetime.now().strftime(
        "screen-%Y%m%d-%H%M%S.mp4")
    ap = argparse.ArgumentParser(
        description="Record the screen or a window, H.264-encoded on the Intel HD 3000. "
                    "Stop with Ctrl+C or GNOME's stop button in the top bar.")
    ap.add_argument("-o", "--output", type=Path, default=default_out,
                    help="output file (default ~/Videos/screen-YYYYMMDD-HHMMSS.mp4)")
    ap.add_argument("-t", "--time", type=float, metavar="SECS",
                    help="stop after SECS seconds (default: until stopped)")
    ap.add_argument("-r", "--fps", type=int, default=30, help="frame rate (default 30)")
    ap.add_argument("-s", "--size", type=parse_size, default=(1920, 1080), metavar="WxH",
                    help="largest output size; the picture is scaled down to fit, "
                         "keeping its shape, never up (default 1920x1080, max 2048x2048)")
    ap.add_argument("-q", "--qp", type=int, default=24, choices=range(0, 52), metavar="QP",
                    help="quality: lower is better and bigger, 18-30 is sensible (default 24)")
    ap.add_argument("-b", "--bitrate", type=parse_rate, metavar="RATE",
                    help="cap the size instead: average bitrate such as 6M (about "
                         "45 MB a minute); replaces -q")
    ap.add_argument("-w", "--window", action="store_true",
                    help="offer windows as well as monitors in GNOME's picker")
    ap.add_argument("-a", "--audio", choices=["none", "desktop", "mic", "both"],
                    default="none", help="record audio too, as AAC (default none)")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="print both halves (for a 2560x1440 screen) and exit")
    args = ap.parse_args()

    Gst.init(None)
    radeon_pp = find_va_element("postproc", ["AMD", "ATI", "Radeon"])
    intel_node = find_render_node("i915")
    if not radeon_pp:
        die("no VA-API video processor on the Radeon (is mesa-va-drivers installed?)")
    if not intel_node:
        die("no Intel (i915) GPU found")

    if args.dry_run:
        w, h = fit(2560, 1440, *args.size)
        print(gst_pipeline(radeon_pp, "<fd>", "<node>", w, h, args.fps, "<pipe>")
              .replace(" ! ", " \\\n  ! "))
        print("  | " + " ".join(ffmpeg_command(intel_node, w, h, args.fps, args.qp,
                                               args.bitrate, args.audio, args.output)))
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)

    portal = Portal()
    fd, node, (src_w, src_h) = portal.start(1 | (2 if args.window else 0))
    if not src_w:  # portal didn't say; assume the panel
        src_w, src_h = 2560, 1440
    w, h = fit(src_w, src_h, *args.size)
    quality = f"{args.bitrate / 1e6:g} Mbit/s" if args.bitrate else f"qp {args.qp}"
    log(f"recording {src_w}x{src_h} -> {w}x{h} @ {args.fps} fps, {quality}, "
        f"audio {args.audio} -> {args.output}")

    # ffmpeg in its own session, so Ctrl+C reaches only us; we then stop the
    # video, and ffmpeg finishes the file when its input ends.
    ff = subprocess.Popen(
        ffmpeg_command(intel_node, w, h, args.fps, args.qp, args.bitrate, args.audio,
                       args.output),
        stdin=subprocess.PIPE, start_new_session=True)
    try:  # bigger pipe (default 64 KiB; a frame is ~8 MB) means fewer wakeups
        fcntl.fcntl(ff.stdin.fileno(), F_SETPIPE_SZ, 1 << 20)
    except OSError:
        pass

    pipeline = Gst.parse_launch(
        gst_pipeline(radeon_pp, fd, node, w, h, args.fps, ff.stdin.fileno()))
    loop = GLib.MainLoop()
    stopping = False

    # A clean stop: end-of-stream travels down the pipeline, fdsink finishes
    # writing, then we close the pipe and ffmpeg finalises the MP4.
    # The EOS is pushed in just after pipewiresrc rather than sent to it, so it
    # still works when GNOME has already torn the stream down.
    def stop(*_):
        nonlocal stopping
        if not stopping:
            stopping = True
            log("stopping...")
            src = pipeline.get_by_name("pipewiresrc0")
            src.get_static_pad("src").get_peer().send_event(Gst.Event.new_eos())
            GLib.timeout_add_seconds(5, lambda: loop.quit() or False)  # safety net
        return True

    def on_message(_bus, msg):
        if msg.type == Gst.MessageType.EOS:
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            # When GNOME's stop button is used, pipewiresrc reports the stream
            # vanishing ("target not found"); that's expected once stopping.
            if not stopping:
                log(f"error: {err.message}")
                stop()

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_message)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal_add(GLib.PRIORITY_DEFAULT, sig, stop)
    portal.on_closed(stop)
    if args.time:
        GLib.timeout_add(int(args.time * 1000), lambda: stop() and False)
    # If ffmpeg dies early (e.g. bad option), stop rather than block on the pipe.
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, ff.pid, lambda *_: stop())

    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        ff.kill()
        die("pipeline failed to start (run with GST_DEBUG=2 for details)")
    log("press Ctrl+C or GNOME's stop button to finish")
    loop.run()
    pipeline.set_state(Gst.State.NULL)

    ff.stdin.close()  # end of video: ffmpeg writes the index and exits
    try:
        ff.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ff.send_signal(signal.SIGINT)
        ff.wait()
    print(file=sys.stderr)

    if ff.returncode == 0 and args.output.exists():
        log(f"saved {args.output} ({args.output.stat().st_size / 1e6:.1f} MB)")
    else:
        die(f"ffmpeg failed (exit {ff.returncode})")


if __name__ == "__main__":
    main()
