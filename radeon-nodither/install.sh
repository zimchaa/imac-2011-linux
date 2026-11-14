#!/bin/bash
# Install or update radeon-nodither. Run with sudo.
set -e
cd "$(dirname "$0")"
install -m 755 radeon-nodither.py /usr/local/sbin/radeon-nodither
install -m 644 radeon-nodither.service /etc/systemd/system/radeon-nodither.service
systemctl daemon-reload
systemctl reenable radeon-nodither
systemctl restart radeon-nodither
echo "radeon-nodither installed. Check with: sudo radeon-nodither"
