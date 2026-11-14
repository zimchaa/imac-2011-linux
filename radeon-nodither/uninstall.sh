#!/bin/bash
# Remove radeon-nodither and restore the driver's dithering. Run with sudo.
cd "$(dirname "$0")"
systemctl disable --now radeon-nodither 2>/dev/null
[ -f /run/radeon-nodither.json ] && /usr/local/sbin/radeon-nodither restore
rm -f /etc/systemd/system/radeon-nodither.service /usr/local/sbin/radeon-nodither
systemctl daemon-reload
echo "radeon-nodither removed."
