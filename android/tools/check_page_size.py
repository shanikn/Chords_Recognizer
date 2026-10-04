"""Check that every native library in an APK or AAB works on 16 KB-page phones, which
Google Play requires of apps with native code: each ELF LOAD segment aligned to 16 KB or
more. Exits 1, naming the libraries, when one isn't.

    python3 android/tools/check_page_size.py android/app/build/outputs/bundle/playRelease/app-play-release.aab
"""

from __future__ import annotations

import struct
import sys
import zipfile

PAGE = 16 * 1024
PT_LOAD = 1


def min_load_align(elf: bytes) -> int:
    if elf[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if elf[4] != 2:
        raise ValueError("not a 64-bit library")
    endian = "<" if elf[5] == 1 else ">"
    phoff, = struct.unpack_from(endian + "Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from(endian + "HH", elf, 0x36)
    aligns = []
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type, = struct.unpack_from(endian + "I", elf, off)
        if p_type == PT_LOAD:
            p_align, = struct.unpack_from(endian + "Q", elf, off + 0x30)
            aligns.append(p_align)
    return min(aligns)


def main(path: str) -> int:
    bad = []
    with zipfile.ZipFile(path) as z:
        libs = [n for n in z.namelist() if n.endswith(".so")]
        for name in libs:
            align = min_load_align(z.read(name))
            print(f"{align:>6}  {name}")
            if align < PAGE:
                bad.append(name)
    if not libs:
        print("no native libraries found")
        return 1
    if bad:
        print(f"Not 16 KB-aligned (Google Play rejects these): {', '.join(bad)}")
        return 1
    print(f"All {len(libs)} native libraries are 16 KB-aligned.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
