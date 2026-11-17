#!/bin/bash
# Record the screen with H.264 encoding on the otherwise idle Intel HD 3000.
#
# The panel is driven by the Radeon, which has no video encoder; the Intel GPU
# has one but isn't connected to anything. So each frame is:
#   1. captured from the Radeon's scanout buffer (ffmpeg kmsgrab, zero-copy)
#   2. scaled down on the Radeon's video processor, staying in RGB
#   3. copied through system RAM to the Intel GPU
#   4. converted RGB -> NV12 (Intel GPU, or the CPU with -c cpu)
#   5. encoded to H.264 on the Intel GPU
# See the README section "Screen recording with the Intel H.264 encoder".
#
# usage: sudo ./screen-record.sh [options]
#   -o FILE   output file (default ~/Videos/screen-YYYYMMDD-HHMMSS.mp4)
#   -t SECS   stop after SECS seconds (default: until Ctrl+C or q)
#   -r FPS    frame rate (default 30)
#   -s WxH    output size, max 2048x2048 (default 1920x1080)
#   -q QP     quality, lower = better and bigger, 18-30 is sensible (default 24)
#   -b RATE   cap the size instead: average bitrate like 6M (replaces -q)
#   -c MODE   gpu = Intel converts colours (default, ~30% of one core)
#             cpu = CPU converts colours (exact colours, ~77% of one core)
#   -n        dry run: print the ffmpeg command and exit
#
# Needs root: kmsgrab reads the display's framebuffer, which only root
# (CAP_SYS_ADMIN) may do. Records video only: no audio, and the mouse pointer
# (a separate hardware cursor plane) is usually not included.
set -euo pipefail

die() { echo "screen-record: $*" >&2; exit 1; }

# The user who ran sudo, so the file lands in their home and is owned by them.
user=${SUDO_USER:-$(id -un)}
home=$(getent passwd "$user" | cut -d: -f6)

out="$home/Videos/screen-$(date +%Y%m%d-%H%M%S).mp4"
secs="" fps=30 size=1920x1080 qp=24 rate="" csc=gpu dry=0

while getopts "o:t:r:s:q:b:c:nh" opt; do
    case $opt in
        o) out=$OPTARG ;;
        t) secs=$OPTARG ;;
        r) fps=$OPTARG ;;
        s) size=$OPTARG ;;
        q) qp=$OPTARG ;;
        b) rate=$OPTARG ;;
        c) csc=$OPTARG ;;
        n) dry=1 ;;
        *) sed -n '/^# usage/,/^#   -n/s/^# \{0,1\}//p' "$0"; exit 1 ;;
    esac
done

# --- check the options ------------------------------------------------------
[[ $size =~ ^([0-9]+)x([0-9]+)$ ]] || die "size must look like 1920x1080"
w=${BASH_REMATCH[1]} h=${BASH_REMATCH[2]}
# Sandy Bridge's encoder rejects anything over 2048 in either direction.
(( w <= 2048 && h <= 2048 )) || die "Intel HD 3000 can't encode wider/taller than 2048"
(( w % 2 == 0 && h % 2 == 0 )) || die "width and height must be even"
[[ $fps =~ ^[0-9]+$ ]] || die "fps must be a whole number"
[[ $qp =~ ^[0-9]+$ ]] && (( qp <= 51 )) || die "qp must be 0-51"
[[ -z $secs || $secs =~ ^[0-9]+([.][0-9]+)?$ ]] || die "seconds must be a number"
[[ -z $rate || $rate =~ ^[0-9]+(\.[0-9]+)?[kKmM]?$ ]] || die "bitrate must look like 6M or 4500k"
[[ $csc == gpu || $csc == cpu ]] || die "-c must be gpu or cpu"

# --- find the two GPUs by driver --------------------------------------------
# Card numbers (card1, renderD129...) depend on probe order, so don't hardcode.
find_node() {   # $1 = driver, $2 = card|renderD
    local n
    for n in /sys/class/drm/"$2"[0-9]*; do
        [[ $(basename "$(readlink -f "$n/device/driver")") == "$1" ]] &&
            { echo "/dev/dri/$(basename "$n")"; return; }
    done
    die "no $1 $2 device found"
}
radeon_card=$(find_node radeon card)       # owns the screen: capture from here
intel_render=$(find_node i915 renderD)     # has the H.264 encoder

# Constant quality (-qp) keeps the picture quality the same and lets the size
# vary: full-screen video can take 15+ Mbit/s. A bitrate cap (-b) switches
# the encoder to variable bitrate averaging that rate, peaks up to 1/3 higher.
if [[ -n $rate ]]; then
    bps=$(numfmt --from=si "${rate^^}")
    ratectl=(-b:v "$bps" -maxrate $((bps * 4 / 3)))
else
    ratectl=(-qp "$qp")
fi

# --- build the filter chain -------------------------------------------------
# kmsgrab hands over the Radeon's framebuffer as a DRM PRIME (dma-buf) frame.
# hwmap wraps that same buffer as a VA-API surface on the Radeon - no copy.
# scale_vaapi then shrinks it on the Radeon's video processor. It must stay
# RGB (bgr0): the Radeon's RGB->NV12 conversion produces wrong, green/magenta
# colours. hwdownload copies the small frame into system RAM (the CPU can't
# read the full-size framebuffer directly - that's a SIGBUS).
radeon="hwmap=derive_device=vaapi,scale_vaapi=w=$w:h=$h:format=bgr0,hwdownload,format=bgr0"
case $csc in
    # hwupload goes to the Intel GPU (-filter_hw_device intel), whose video
    # processor converts to NV12, the format the encoder takes.
    gpu) vf="$radeon,hwupload,scale_vaapi=format=nv12" ;;
    # swscale converts to NV12 on the CPU, then upload to the Intel GPU.
    cpu) vf="$radeon,format=nv12,hwupload" ;;
esac

cmd=(ffmpeg -hide_banner -loglevel warning -stats -y
     -init_hw_device "vaapi=intel:$intel_render"   # the Intel GPU, for upload + encode
     -filter_hw_device intel
     # kmsgrab ignores the input name; don't use "-", which ffmpeg treats as
     # stdin and then stops listening for the q key.
     -device "$radeon_card" -framerate "$fps" -f kmsgrab -i screen
     ${secs:+-t "$secs"}
     -vf "$vf"
     -c:v h264_vaapi "${ratectl[@]}"                # Intel encoder
     -movflags +faststart                           # index at the front, for playback/sharing
     "$out")

if (( dry )); then
    printf '%q ' "${cmd[@]}"; echo
    exit 0
fi

(( EUID == 0 )) || die "needs root to capture the screen: sudo $0 $*"

# Create the output folder as the user, not root.
outdir=$(dirname "$out")
[[ -d $outdir ]] || install -d -o "$user" -g "$(id -gn "$user")" "$outdir"

echo "Recording ${size} @ ${fps} fps (colour conversion: $csc) -> $out"
[[ -z $secs ]] && echo "Press q or Ctrl+C to stop."

# Ctrl+C goes to ffmpeg too, which finishes the file cleanly; ignore it here
# so the script carries on to fix the file's owner.
trap '' INT
# libva first tries the newer iHD driver, which doesn't support Sandy Bridge,
# so expect harmless "iHD_drv_video.so init failed" / "hybrid_drv_video.so"
# lines before it settles on the i965 driver.
"${cmd[@]}" || true
trap - INT

[[ -s $out ]] || die "nothing recorded"
chown "$user:$(id -gn "$user")" "$out"
echo "Saved $out ($(du -h "$out" | cut -f1))"
