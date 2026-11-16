#!/bin/bash
# Show which GPU a running program is rendering on.
#   ./which-gpu.sh hl2_linux      (process name)
#   ./which-gpu.sh 12345          (PID)
# With no argument, lists every process that has a GPU open.
set -e

gpu_name() {   # /dev/dri/renderD128 -> "01:00.0 radeon"
    local node=${1##*/} dev
    dev=$(readlink -f /sys/class/drm/$node/device)
    printf '%s %s' "${dev##*/0000:}" "$(basename "$(readlink -f "$dev/driver")")"
}

report() {
    local pid=$1 found=
    local name; name=$(cat /proc/$pid/comm 2>/dev/null) || return 0
    local nodes; nodes=$(for fd in /proc/$pid/fd/*; do readlink "$fd" 2>/dev/null; done | grep '^/dev/dri/' | sort | uniq -c) || true
    [ -n "$nodes" ] || return 0
    echo "$pid $name"
    while read -r count node; do
        echo "    $count x $node  ($(gpu_name "$node"))"
    done <<< "$nodes"
    local prime; prime=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep '^DRI_PRIME=') || true
    [ -n "$prime" ] && echo "    environment: $prime"
    return 0
}

if [ -z "$1" ]; then
    for p in /proc/[0-9]*; do report "${p#/proc/}"; done
elif [[ $1 =~ ^[0-9]+$ ]]; then
    report "$1"
else
    pids=$(pgrep -x "$1") || { echo "no process called $1" >&2; exit 1; }
    for p in $pids; do report "$p"; done
fi
