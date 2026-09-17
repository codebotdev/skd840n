#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Header/bounds tests using synthetic files; no firmware compilation."""

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "check_image", Path(__file__).with_name("check-image.py")
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ImageBoundsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "image"

    def image(self, offset=0, footprint=0x04000000, flags=0, size=64):
        header = bytearray(64)
        struct.pack_into("<QQQ", header, 8, offset, footprint, flags)
        header[56:60] = b"ARM\x64"
        with self.path.open("wb") as stream:
            stream.write(header)
            stream.truncate(size)

    def fit(self, total=40, size=40, magic=0xD00DFEED):
        with self.path.open("wb") as stream:
            stream.write(struct.pack(">II", magic, total))
            stream.truncate(size)

    def test_valid_image_including_bss_limit(self):
        self.image()
        checker.check_image(self.path)

    def test_old_kernel_load_offset_rejected(self):
        self.image(offset=0x80000)
        with self.assertRaises(ValueError):
            checker.check_image(self.path)

    def test_bss_overflow_rejected_even_with_small_file(self):
        self.image(footprint=0x04000001)
        with self.assertRaises(ValueError):
            checker.check_image(self.path)

    def test_invalid_image_headers(self):
        for options in ({"footprint": 0}, {"footprint": 63}, {"flags": 1},
                        {"size": 32}, {"size": 65, "footprint": 64}):
            with self.subTest(options=options):
                self.image(**options)
                with self.assertRaises(ValueError):
                    checker.check_image(self.path)

    def test_wrong_image_magic(self):
        self.path.write_bytes(bytes(64))
        with self.assertRaises(ValueError):
            checker.check_image(self.path)

    def test_valid_fit_at_transfer_limit(self):
        self.fit(total=0x02000000, size=0x02000000)
        checker.check_fit(self.path)

    def test_invalid_fit_headers_and_transfer_overflow(self):
        for options in ({"size": 7}, {"magic": 0}, {"total": 41},
                        {"total": 39}, {"size": 0x02000001}):
            with self.subTest(options=options):
                self.fit(**options)
                with self.assertRaises(ValueError):
                    checker.check_fit(self.path)


if __name__ == "__main__":
    unittest.main()
