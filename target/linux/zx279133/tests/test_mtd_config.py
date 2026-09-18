#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Regression checks for explicit RAM-bring-up storage configuration.

Only reads config text. No make, compiler, Kconfig shell probes or hardware.
The merge example models assignment precedence, NOT complete Kconfig
visibility, select/imply dependencies or the user's top-level configuration.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISABLED = (
    "MTD_BLOCK", "MTD_BLOCK_RO", "MTD_CMDLINE_PARTS",
    "MTD_PARTITIONED_MASTER", "MTD_RAW_NAND", "MTD_SPI_NOR", "MTD_UBI",
    "SPI_SLAVE", "SPI_SPIDEV",
)
ENABLED = (
    "MTD", "MTD_NAND_CORE", "MTD_NAND_ECC", "MTD_OF_PARTS", "MTD_SPI_NAND",
    "SPI", "SPI_MASTER", "SPI_MEM", "SPI_SKD840N_RO", "SKD840N_MDIO_DIAG",
)


def parse_config(text):
    """Preserve missing versus explicit n; reject duplicate assignments."""
    values = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"CONFIG_(\w+)=(.*)", line)
        unset = re.fullmatch(r"# CONFIG_(\w+) is not set", line)
        if match:
            name, value = match.groups()
        elif unset:
            name, value = unset.group(1), "n"
        else:
            if line.startswith(("CONFIG_", "# CONFIG_")):
                raise ValueError(f"malformed config line {lineno}: {line}")
            continue
        if name in values:
            raise ValueError(f"duplicate CONFIG_{name} on line {lineno}")
        values[name] = value
    return values


def require_disabled(values, name):
    # Never use get(name, "n"): that would hide the original missing answer.
    if values.get(name) != "n":
        raise ValueError(f"CONFIG_{name} must be explicitly disabled")


class StorageConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "config-6.12").read_text(encoding="utf-8")
        cls.config = parse_config(cls.text)

    def test_block_and_other_storage_exclusions_are_explicit(self):
        for name in DISABLED:
            with self.subTest(symbol=name):
                require_disabled(self.config, name)

    def test_character_mtd_and_diagnostics_are_not_disabled_as_a_workaround(self):
        for name in ENABLED:
            with self.subTest(symbol=name):
                self.assertEqual(self.config.get(name), "y")

    def test_missing_is_not_explicit_n(self):
        self.assertNotIn("MTD_BLOCK_RO", parse_config(""))
        self.assertEqual(
            parse_config("# CONFIG_MTD_BLOCK_RO is not set\n"),
            {"MTD_BLOCK_RO": "n"},
        )
        with self.assertRaises(ValueError):
            require_disabled({}, "MTD_BLOCK_RO")

    def test_old_missing_answer_is_detected_after_target_override(self):
        # Relevant generic defaults at 6f46d7ed: MTD=y, MTD_BLOCK=y,
        # no MTD_BLOCK_RO answer. Linux 6.12.103 drivers/mtd/Kconfig:
        # MTD_BLOCK_RO depends on MTD_BLOCK!=y && BLOCK (inside if MTD).
        # BLOCK=y is an assumption for this reduced example, not a probe.
        generic = parse_config("CONFIG_BLOCK=y\nCONFIG_MTD=y\nCONFIG_MTD_BLOCK=y\n")
        old = dict(self.config)
        old.pop("MTD_BLOCK_RO", None)
        merged = {**generic, **old}
        self.assertEqual(merged["MTD"], "y")
        self.assertEqual(merged["BLOCK"], "y")
        self.assertEqual(merged["MTD_BLOCK"], "n")
        with self.assertRaises(ValueError):
            require_disabled(merged, "MTD_BLOCK_RO")
        require_disabled({**generic, **self.config}, "MTD_BLOCK_RO")

    def test_enabling_the_alternative_block_driver_is_rejected(self):
        for value in ("y", "m"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_disabled({"MTD_BLOCK_RO": value}, "MTD_BLOCK_RO")

    def test_duplicates_are_rejected(self):
        for second in ("CONFIG_MTD_BLOCK_RO=y",
                       "# CONFIG_MTD_BLOCK_RO is not set"):
            with self.subTest(second=second), self.assertRaises(ValueError):
                parse_config("# CONFIG_MTD_BLOCK_RO is not set\n" + second)

    def test_malformed_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_config("# CONFIG_MTD_BLOCK_RO is not se")


if __name__ == "__main__":
    unittest.main(verbosity=2)
