# Plex on Android TV without Google Play services

Plex for Android TV 2026.19.1 and 2026.20.0 (the React Native rewrite) crash about three seconds after launch on TVs that have no Google Play services, such as China-region Android TVs. The last native build, 10.30.8.4222, runs fine on the same TV.

`patch_plex.py` and `build_patched.sh` patch your own copy of the APKs so the new app starts. `hold/` is for staying on 10.30.8.4222 instead: it stops stores from installing a 2026.x build over it.

Nothing from Plex is in this repo. You supply the APKs.

## The crash

```
FATAL EXCEPTION: mqt_v_native
Process: com.plexapp.android
java.lang.RuntimeException: Call setApplication from Application onCreate before using this API
    at tv.vizbee.screen.api.Vizbee.initialize
    at tv.vizbee.rnreceiver.RNVizbeeNativeManager.init
    at com.facebook.jni.NativeRunnable.run(Native Method)
```

## Cause

`tv.plex.app.MainApplication.onCreate` sets up the Vizbee receiver SDK (casting from phones) only on TVs where Google Play services are available. Paraphrased from the 2026.20.0 bytecode:

```kotlin
if (uiMode == UI_MODE_TYPE_TELEVISION && isGooglePlayServicesAvailable(this) == SUCCESS) {
    Vizbee.getInstance().setApplication(this)
} else {
    log("[Vizbee] Receiver SDK not available (e.g. generic build on Android TV), skipping init")
}
```

The JavaScript side doesn't check the same thing. It calls `RNVizbeeNativeManager.init()` on every TV, `Vizbee.initialize()` throws because `setApplication()` never ran, and the exception kills the app on the React Native native-modules thread. On a TV without Play services that happens on every launch.

## What the patch changes

Five methods of `tv.vizbee.rnreceiver.RNVizbeeNativeManager` return immediately: `init`, `setPlayerAdapter`, `resetPlayerAdapter`, `setVideoStatus` and `enableVerboseLogging`. Outside the Vizbee SDK itself, they and the guarded call above are the only code that touches Vizbee, and the native side already skips Vizbee on these devices, so casting through Vizbee stays off as it already was.

`patch_plex.py` finds the class in whichever `classes*.dex` holds it, replaces the five method bodies with `return-void`, fixes the dex checksum and SHA-1, and repacks every APK of the set without the old signatures. If a later release renames those methods or changes their signatures, it stops with an error. `build_patched.sh` runs it, then aligns and signs the APKs with your key.

## Before you do this

- The result is signed with your key, not Plex's. The first time, you have to uninstall the official app, which signs you out and clears Plex's settings on the TV.
- Stores can't update it any more: Android rejects their installs with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`. Each new Plex release needs patching again.
- Keep the keystore. A later patched build installs over the current one, sign-in included, only if it's signed with the same key. Without it you're back to uninstalling.
- Without Play services there are no push notifications and no in-app purchases on the TV. Logcat errors from FirebaseMessaging, the advertising ID and RnIap's billing connection are expected and don't crash anything.
- It's a modified copy of Plex's app for your own device. Don't share the patched APKs.

## Usage

You need Python 3.8 or newer, the Android SDK build-tools (`zipalign`, `apksigner`), a JDK (the one bundled with Android Studio works) and `adb`. The scripts are bash and were tested in Git Bash on Windows.

1. Put the Plex APK set for your TV in one folder: `base.apk` plus its `split_config.*.apk` files. You can pull it from a device that has the official 2026.x installed (installing works, only launching crashes):

   ```sh
   adb shell pm path com.plexapp.android
   adb pull /data/app/.../base.apk plex-apks/
   ```

   In Git Bash, prefix those adb commands with `MSYS_NO_PATHCONV=1`, otherwise it rewrites the `/data/...` paths into Windows paths. You can also download the Android TV bundle (`.apkm`) from APKMirror. It's a zip: unzip `base.apk` and the splits for your ABI, screen density and language.

2. Build. The first run needs `--new-key` to create `plex-patched.jks` next to the script:

   ```sh
   export ANDROID_HOME=/path/to/Android/Sdk
   ./build_patched.sh --new-key plex-apks patched
   ```

   Later runs reuse that keystore (or `$KEYSTORE`). Set `KEYSTORE_PASS` to skip the password prompts.

3. Install. The first time, remove the official app:

   ```sh
   adb uninstall com.plexapp.android
   adb install-multiple patched/*.apk
   ```

   For later releases, skip the uninstall and run `adb install-multiple -r patched/*.apk`. You stay signed in.

## Staying on 10.30.8.4222 instead

`hold/build.sh` builds `plex-update-hold.apk`, an app with no code and no launcher icon. It declares five content-provider authorities that only the React Native builds use, `com.plexapp.android.reactnativefirebaseappinitprovider` among them. Android refuses to install a package whose provider authority another package already owns, so while the hold app is installed, a 2026.x install from any store should fail with `INSTALL_FAILED_CONFLICTING_PROVIDER`.

```sh
./hold/build.sh
adb install hold/plex-update-hold.apk
```

Install it once you're back on 10.30.8.4222. It can't go on while a 2026.x build is installed, for the same reason. Only that direction was tested here; a store installing 2026.x over the hold app goes through the same check but wasn't tried. Uninstall it when Plex ships a build that works.

On the Android 12 TV used here, `adb install-multiple -r -d` with the 10.30.8.4222 APKs downgraded 2026.20.0 and kept the sign-in.

## Tested on

Plex for Android TV 2026.20.0 (versionCode 971050504) on a Sony BRAVIA KD-43X85J with China firmware: Android 12, 32-bit ARM, no Google Play services. Sign-in and playback work.

This project isn't affiliated with Plex.
