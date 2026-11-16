#!/bin/bash
# Tomb Raider (2013, Feral's Linux port) exits at launch with
#   error while loading shared libraries: libssl.so.1.0.0 / libicui18n.so.51
# because the game looks in lib/ but its bundled libraries are in lib/i686/.
# This links them into lib/. Re-run after "Verify integrity of game files".
set -e
game="${STEAM_DIR:-$HOME/.local/share/Steam}/steamapps/common/Tomb Raider"
cd "$game/lib"
for f in i686/*.so*; do ln -sf "$f" "$(basename "$f")"; done
echo "linked $(ls i686 | wc -l) libraries in $game/lib"
