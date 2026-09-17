#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reject images outside the documented SK-D840N RAM boot layout."""

import argparse
from pathlib import Path
import struct
import sys


def check_image(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(64)
    if len(header) != 64 or header[56:60] != b"ARM\x64":
        raise ValueError("missing ARM64 Image header")
    offset, image_size, flags = struct.unpack_from("<QQQ", header, 8)
    if offset != 0:
        raise ValueError("text_offset must be zero for FIT load/entry 0x80000000")
    if flags & 1:
        raise ValueError("big-endian kernels are unsupported")
    # Include BSS and the embedded initramfs in the RAM footprint check.
    if not 64 <= size <= image_size <= 0x04000000:
        raise ValueError("Image size/footprint invalid or greater than 64 MiB")


def check_fit(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(8)
    if len(header) != 8:
        raise ValueError("truncated FIT header")
    magic, total = struct.unpack(">II", header)
    if magic != 0xD00DFEED or not 40 <= total <= size <= 0x02000000:
        raise ValueError("invalid FIT header/size or FIT greater than 32 MiB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("image", "fit"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        {"image": check_image, "fit": check_fit}[args.kind](args.path)
    except (OSError, ValueError) as error:
        print(f"SK-D840N: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
