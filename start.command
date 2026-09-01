#!/bin/bash
# Double-click this file in Finder to start the game server.
cd "$(dirname "$0")" || exit 1

THREE="three.min.js"
URL="https://cdn.jsdelivr.net/npm/three@0.152.2/build/three.min.js"

need_download() {
  [ ! -f "$THREE" ] && return 0
  # Guard against a captive-portal HTML error page having been saved as the
  # engine, which would otherwise show up as a blank screen with no clue why.
  size=$(wc -c < "$THREE" | tr -d ' ')
  [ "$size" -lt 100000 ] && return 0
  head -c 200 "$THREE" | grep -qi "<html" && return 0
  return 1
}

if need_download; then
  echo "  Fetching the 3D engine (one time, needs internet)..."
  if curl -fsSL --max-time 60 "$URL" -o "$THREE.tmp"; then
    size=$(wc -c < "$THREE.tmp" | tr -d ' ')
    if [ "$size" -gt 100000 ] && ! head -c 200 "$THREE.tmp" | grep -qi "<html"; then
      mv "$THREE.tmp" "$THREE"
      echo "  Done (${size} bytes)."
    else
      rm -f "$THREE.tmp"
      echo "  !! Download looked wrong (${size} bytes). The page will fall back to the CDN."
    fi
  else
    rm -f "$THREE.tmp"
    echo "  !! Could not download. The page will fall back to the CDN if you have internet."
  fi
fi

exec python3 server.py "$@"
