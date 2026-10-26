#!/usr/bin/env bash
# Patches a Plex for Android TV 2026.x APK set so it starts without Google Play services, then aligns and re-signs it.
# Usage: build_patched.sh [--new-key] <dir with base.apk and split_*.apk> <output dir>
# Env: ANDROID_HOME (Android SDK), JAVA_HOME (JDK 17), KEYSTORE (default: plex-patched.jks next to this script),
#      KEYSTORE_PASS (otherwise keytool and apksigner prompt for it).
set -euo pipefail
KIT=$(cd "$(dirname "$0")" && pwd)
KS=${KEYSTORE:-$KIT/plex-patched.jks}
NEW_KEY=0
if [ "${1:-}" = "--new-key" ]; then NEW_KEY=1; shift; fi
[ $# -eq 2 ] || { echo "usage: $0 [--new-key] <apk dir> <output dir>" >&2; exit 2; }
IN=$1
OUT=$2

SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
[ -n "$SDK" ] || { echo "set ANDROID_HOME to your Android SDK" >&2; exit 1; }
BT=$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)
tool() {
  for n in "$1" "$1.exe" "$1.bat"; do
    if [ -f "$BT/$n" ]; then echo "$BT/$n"; return; fi
  done
  echo "no $1 in $BT" >&2
  exit 1
}
ZIPALIGN=$(tool zipalign)
APKSIGNER=$(tool apksigner)
KEYTOOL=${JAVA_HOME:+$JAVA_HOME/bin/}keytool
PYTHON=
# On Windows "python3" can be the Microsoft Store stub, so test that each candidate really runs.
for p in python3 python; do
  if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then PYTHON=$p; break; fi
done
[ -n "$PYTHON" ] || { echo "needs Python 3.8 or newer" >&2; exit 1; }

if [ ! -f "$KS" ]; then
  # Updates only install over a patched Plex signed with the same key, so a key is never created silently.
  [ $NEW_KEY = 1 ] || { echo "no keystore at $KS (first run? add --new-key)" >&2; exit 1; }
  "$KEYTOOL" -genkeypair -keystore "$KS" -storetype PKCS12 -alias plex -keyalg RSA -keysize 2048 \
    -validity 36500 -dname "CN=Plex patched locally" ${KEYSTORE_PASS:+-storepass:env KEYSTORE_PASS -keypass:env KEYSTORE_PASS}
fi

"$PYTHON" -I "$KIT/patch_plex.py" "$IN" "$OUT"
for f in "$OUT"/*.unaligned.apk; do
  "$ZIPALIGN" -f -p 4 "$f" "${f%.unaligned.apk}.apk"
  rm "$f"
done
for a in "$OUT"/*.apk; do
  "$APKSIGNER" sign --ks "$KS" --ks-key-alias plex --v4-signing-enabled false ${KEYSTORE_PASS:+--ks-pass env:KEYSTORE_PASS} "$a"
done
"$APKSIGNER" verify --print-certs "$OUT/base.apk" | grep -m1 'certificate SHA-256'
echo "done: install with  adb install-multiple -r $OUT/*.apk"
