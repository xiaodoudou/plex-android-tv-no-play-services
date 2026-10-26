"""Makes Plex for Android TV 2026.x start on devices without Google Play services.

MainApplication only sets up the Vizbee receiver SDK when Play services are available, but the
React Native side calls RNVizbeeNativeManager.init regardless, and Vizbee throws. This turns the
bridge methods that reach into Vizbee into no-ops and repacks the APK set, unsigned.

Usage: python -I patch_plex.py <dir with base.apk and split_*.apk> <output dir>
"""
import hashlib
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path

CLASS = "Ltv/vizbee/rnreceiver/RNVizbeeNativeManager;"
METHODS = {"init", "setPlayerAdapter", "resetPlayerAdapter", "setVideoStatus", "enableVerboseLogging"}
# Old signatures and Play's source stamp stop matching once the APKs are re-signed.
DROP = re.compile(r"^(stamp-cert-sha256|META-INF/MANIFEST\.MF|META-INF/[^/]+\.(SF|RSA|DSA|EC))$")


def uleb128(buf, pos):
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


class Dex:
    def __init__(self, data):
        if data[:4] != b"dex\n":
            raise ValueError("not a dex file")
        self.d = data

    def u2(self, off):
        return struct.unpack_from("<H", self.d, off)[0]

    def u4(self, off):
        return struct.unpack_from("<I", self.d, off)[0]

    def string(self, idx):
        _, off = uleb128(self.d, self.u4(self.u4(60) + 4 * idx))
        return bytes(self.d[off:self.d.index(0, off)]).decode("utf-8", "replace")

    def type_name(self, idx):
        return self.string(self.u4(self.u4(68) + 4 * idx))

    def methods_of(self, descriptor):
        """Yields (name, return type, code_item offset) for each method of the class."""
        for i in range(self.u4(96)):
            class_def = self.u4(100) + 32 * i
            if self.type_name(self.u4(class_def)) != descriptor:
                continue
            pos = self.u4(class_def + 24)
            counts = []
            for _ in range(4):
                n, pos = uleb128(self.d, pos)
                counts.append(n)
            for _ in range(2 * (counts[0] + counts[1])):
                _, pos = uleb128(self.d, pos)
            for count in counts[2:]:
                idx = 0
                for _ in range(count):
                    diff, pos = uleb128(self.d, pos)
                    _, pos = uleb128(self.d, pos)
                    code, pos = uleb128(self.d, pos)
                    idx += diff
                    method_id = self.u4(92) + 8 * idx
                    proto_id = self.u4(76) + 12 * self.u2(method_id + 2)
                    yield self.string(self.u4(method_id + 4)), self.type_name(self.u4(proto_id + 4)), code
            return


def patch(data):
    """Patches the bridge methods in place; returns their names, or [] if the class is not in this dex."""
    done = []
    for name, ret, code in Dex(data).methods_of(CLASS):
        if name not in METHODS:
            continue
        if ret != "V" or not code:
            sys.exit(f"{name} no longer returns void or has no code: the patch needs updating")
        insns = code + 16
        units = struct.unpack_from("<I", data, code + 12)[0]
        # return-void, then nops: the old body stays the same length and still decodes.
        data[insns:insns + 2 * units] = b"\x0e\x00" + b"\x00\x00" * (units - 1)
        done.append(name)
    if done:
        missing = METHODS - set(done)
        if missing:
            sys.exit(f"not found in {CLASS}: {', '.join(sorted(missing))}")
        data[12:32] = hashlib.sha1(data[32:]).digest()
        data[8:12] = struct.pack("<I", zlib.adler32(bytes(data[12:])) & 0xFFFFFFFF)
    return done


def repack(src, dst, replace):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            if DROP.match(info.filename):
                continue
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.compress_type = info.compress_type
            entry.external_attr = info.external_attr
            zout.writestr(entry, replace.get(info.filename) or zin.read(info))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    if not (src / "base.apk").is_file():
        sys.exit(f"no base.apk in {src}")
    apks = [src / "base.apk"] + sorted(src.glob("split_*.apk"))

    replaced = {}
    with zipfile.ZipFile(src / "base.apk") as z:
        for name in sorted(n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)):
            data = bytearray(z.read(name))
            done = patch(data)
            if done:
                replaced[name] = bytes(data)
                print(f"{name}: {', '.join(sorted(set(done)))} now return immediately")
    if not replaced:
        sys.exit(f"{CLASS} is not in base.apk: not a React Native Plex build?")

    out.mkdir(parents=True, exist_ok=True)
    for apk in apks:
        repack(apk, out / f"{apk.stem}.unaligned.apk", replaced if apk.name == "base.apk" else {})
        print(f"repacked {apk.name}")


main()
