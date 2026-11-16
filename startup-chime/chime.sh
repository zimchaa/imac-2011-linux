#!/bin/bash
# Show or mute the Mac startup chime ("bong") from Linux.
#   ./chime.sh          show the current setting
#   sudo ./chime.sh mute
# The setting lives in the firmware's NVRAM (EFI variable SystemAudioVolume),
# so it survives reinstalling Linux. A PRAM/NVRAM reset (hold Cmd+Opt+P+R at
# power-on) clears it and brings the chime back.
set -e
VAR=/sys/firmware/efi/efivars/SystemAudioVolume-7c436110-ab2a-4bbb-a880-fe41995c9f82

show() {
    if [ ! -e "$VAR" ]; then
        echo "SystemAudioVolume not set: chime plays at the default volume"
        return
    fi
    # 4 bytes of attributes, then 1 byte of value; bit 0x80 means muted
    val=$(od -An -j4 -N1 -tu1 "$VAR" | tr -d ' ')
    printf 'SystemAudioVolume = 0x%02x -> chime %s\n' "$val" \
        "$( [ $((val & 0x80)) -ne 0 ] && echo muted || echo 'on')"
}

case "${1:-}" in
    "") show ;;
    mute)
        [ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
        [ -e "$VAR" ] && chattr -i "$VAR"
        # attributes 0x07 = non-volatile + boot-service + runtime access
        printf '\x07\x00\x00\x00\x80' > "$VAR"
        # efivarfs files are immutable by default; set it again to match
        chattr +i "$VAR"
        show ;;
    *) echo "usage: $0 [mute]" >&2; exit 1 ;;
esac
