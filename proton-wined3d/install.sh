#!/bin/bash
# Copy user_settings.py into every installed Proton version. Run as your own
# user (not sudo). Run it again after installing a new Proton version.
set -e
src="$(cd "$(dirname "$0")" && pwd)/user_settings.py"
steam="${STEAM_DIR:-$HOME/.local/share/Steam}"

# Steam library folders, plus custom Proton builds such as GE-Proton
dirs=$(sed -n 's/^\s*"path"\s*"\(.*\)"/\1/p' "$steam/steamapps/libraryfolders.vdf" 2>/dev/null)
found=0
while IFS= read -r p; do
    [ -f "$p/proton" ] || continue
    found=1
    if [ -f "$p/user_settings.py" ] && ! cmp -s "$src" "$p/user_settings.py"; then
        echo "skipped (has its own user_settings.py): $p"
        continue
    fi
    cp "$src" "$p/user_settings.py"
    echo "installed: $p"
done < <(
    printf '%s\n' "$dirs" | while IFS= read -r lib; do
        [ -n "$lib" ] && ls -d "$lib"/steamapps/common/Proton* 2>/dev/null
    done
    ls -d "$steam"/compatibilitytools.d/*/ 2>/dev/null | sed 's|/$||'
)
[ "$found" -eq 1 ] || echo "No Proton found. Run a Windows game once so Steam installs Proton, then run this again."
