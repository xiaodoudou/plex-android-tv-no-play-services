"""Makes Plex for Android TV 2026.x start on devices without Google Play services.

MainApplication only calls Vizbee.setApplication when Play services are available, but the React Native
side calls Vizbee.initialize regardless, and initialize throws when setApplication never ran. This turns
that throw into a return: with Play services the app runs stock code, without them Vizbee stays off.
The APK set is repacked unsigned.

Usage: python -I patch_plex.py <dir with base.apk and split_*.apk> <output dir>
"""
import hashlib
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path

CLASS = "Ltv/vizbee/screen/api/Vizbee;"
METHOD = ("initialize", "(Ljava/lang/String;Ltv/vizbee/screen/api/adapter/VizbeeAppAdapter;Ltv/vizbee/screen/api/VizbeeOptions;)V")
EXCEPTION = "Ljava/lang/RuntimeException;"
MESSAGE = "Call setApplication from Application onCreate before using this API"
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

    def string_index(self, value):
        return next((i for i in range(self.u4(56)) if self.string(i) == value), None)

    def type_name(self, idx):
        return self.string(self.u4(self.u4(68) + 4 * idx))

    def type_index(self, descriptor):
        return next((i for i in range(self.u4(64)) if self.type_name(i) == descriptor), None)

    def signature(self, proto_idx):
        proto = self.u4(76) + 12 * proto_idx
        params, plist = [], self.u4(proto + 8)
        if plist:
            params = [self.type_name(self.u2(plist + 4 + 2 * i)) for i in range(self.u4(plist))]
        return "(" + "".join(params) + ")" + self.type_name(self.u4(proto + 4))

    def methods_of(self, descriptor):
        """Yields (name, signature, code_item offset) for each method of the class."""
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
                    yield self.string(self.u4(method_id + 4)), self.signature(self.u2(method_id + 2)), code
            return


def patch(data):
    """Patches the setApplication guard in place; returns False if Vizbee is not in this dex."""
    dex = Dex(data)
    code = next((c for name, sig, c in dex.methods_of(CLASS) if (name, sig) == METHOD), None)
    if code is None:
        return False
    exc, msg = dex.type_index(EXCEPTION), dex.string_index(MESSAGE)
    if not code or exc is None or msg is None:
        sys.exit("Vizbee.initialize no longer looks the same: the patch needs updating")

    # throw new RuntimeException(MESSAGE) starts with new-instance (22 AA TTTT), then const-string (1a BB SSSS)
    # or const-string/jumbo (1b BB SSSSSSSS).
    new_instance = struct.pack("<H", exc)
    loads = [(0x1b, struct.pack("<I", msg))]
    if msg <= 0xFFFF:
        loads.append((0x1a, struct.pack("<H", msg)))
    insns = code + 16
    body = bytes(data[insns:insns + 2 * dex.u4(code + 12)])
    hits = [
        pos for pos in range(0, len(body) - 6, 2)
        if body[pos] == 0x22 and body[pos + 2:pos + 4] == new_instance
        and any(body[pos + 4] == op and body[pos + 6:pos + 6 + len(idx)] == idx for op, idx in loads)
    ]
    if len(hits) != 1:
        sys.exit(f"expected one throw of '{MESSAGE}' in Vizbee.initialize, found {len(hits)}: the patch needs updating")
    # return-void + nop over the new-instance: Vizbee has already logged its error, the throw becomes unreachable.
    data[insns + hits[0]:insns + hits[0] + 4] = b"\x0e\x00\x00\x00"

    data[12:32] = hashlib.sha1(data[32:]).digest()
    data[8:12] = struct.pack("<I", zlib.adler32(bytes(data[12:])) & 0xFFFFFFFF)
    return True


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
            if patch(data):
                replaced[name] = bytes(data)
                print(f"{name}: Vizbee.initialize now returns instead of throwing when setApplication never ran")
    if not replaced:
        sys.exit(f"{CLASS} is not in base.apk: not a React Native Plex build?")

    out.mkdir(parents=True, exist_ok=True)
    for apk in apks:
        repack(apk, out / f"{apk.stem}.unaligned.apk", replaced if apk.name == "base.apk" else {})
        print(f"repacked {apk.name}")


main()
