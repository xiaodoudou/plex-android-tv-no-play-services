#!/usr/bin/env bash
# Builds plex-update-hold.apk: no code, only content-provider authorities that the React Native Plex builds
# declare, so Android refuses to install those builds while it is installed.
# Env: ANDROID_HOME, JAVA_HOME, KEYSTORE (default: hold.jks next to this script), KEYSTORE_PASS (otherwise prompted).
set -euo pipefail
HOLD=$(cd "$(dirname "$0")" && pwd)
KS=${KEYSTORE:-$HOLD/hold.jks}
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
[ -n "$SDK" ] || { echo "set ANDROID_HOME to your Android SDK" >&2; exit 1; }
BT=$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)
JAR=$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)/android.jar
tool() {
  for n in "$1" "$1.exe" "$1.bat"; do
    if [ -f "$BT/$n" ]; then echo "$BT/$n"; return; fi
  done
  echo "no $1 in $BT" >&2
  exit 1
}
KEYTOOL=${JAVA_HOME:+$JAVA_HOME/bin/}keytool

"$(tool aapt2)" link -o "$HOLD/unsigned.apk" -I "$JAR" --manifest "$HOLD/AndroidManifest.xml"
"$(tool zipalign)" -f -p 4 "$HOLD/unsigned.apk" "$HOLD/aligned.apk"
[ -f "$KS" ] || "$KEYTOOL" -genkeypair -keystore "$KS" -storetype PKCS12 -alias hold -keyalg RSA -keysize 2048 \
  -validity 36500 -dname "CN=Plex update hold" ${KEYSTORE_PASS:+-storepass:env KEYSTORE_PASS -keypass:env KEYSTORE_PASS}
"$(tool apksigner)" sign --ks "$KS" --ks-key-alias hold --v4-signing-enabled false ${KEYSTORE_PASS:+--ks-pass env:KEYSTORE_PASS} \
  --out "$HOLD/plex-update-hold.apk" "$HOLD/aligned.apk"
rm -f "$HOLD/unsigned.apk" "$HOLD/aligned.apk"
echo "built $HOLD/plex-update-hold.apk"
