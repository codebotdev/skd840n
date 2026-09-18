#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline shell and evidence-consistency tests; no compiler or hardware I/O."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "base-files/usr/sbin/skd840n-port-map"
HEADER = "candidate clause mmd phy_id ctrl stat_first stat_now link result"


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / "port-map"
        self.script.write_text(SCRIPT.read_text().replace(
            "base=/sys/bus/platform/devices", "base=" + str(self.root)))
        self.reads = self.root / "reads"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        cat = self.bin / "cat"
        cat.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$READ_LOG"\nexec /bin/cat "$@"\n')
        cat.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        READ_LOG=str(self.reads))
        for dev in ("14f01000.mdio", "14f02000.mdio"):
            directory = self.root / dev
            directory.mkdir()
            (directory / "phy_links").write_text(
                HEADER + "\n0b 22 -1 84b95032 1140 7969 796d up ok\n")
            (directory / "phy_links_c45").write_text(
                HEADER + "\n05 45 1 001cc849 2058 0002 0006 up ok\n"
                "05 45 3 001cc849 205c 0582 0582 down ok\n")

    def run_script(self, *args):
        return subprocess.run(["sh", str(self.script), *args], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_syntax(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True, timeout=5)

    def test_labeled_output_only_adds_case_line(self):
        for mode in ("--c22", "--c45"):
            original = self.run_script(mode)
            labeled = self.run_script(mode, "--label", "lan1")
            self.assertEqual(original.returncode, 0, original.stderr)
            self.assertEqual(labeled.returncode, 0, labeled.stderr)
            self.assertEqual(labeled.stdout, "CASE lan1\n" + original.stdout)
            self.assertEqual(labeled.stderr, original.stderr)

    def test_label_never_selects_a_bus_or_phy(self):
        for label in ("lan1", "lan2", "lan3", "lan4", "all-unplugged", "X_0.test-2"):
            self.reads.unlink(missing_ok=True)
            result = self.run_script("--c22", "--label", label)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.reads.read_text().splitlines(), [
                str(self.root / dev / "phy_links")
                for dev in ("14f01000.mdio", "14f02000.mdio")])

    def test_invalid_arguments_do_not_read(self):
        for args in ((), ("lan1",), ("--c22", "extra"), ("--invalid",),
                     ("--label", "lan1"), ("--c22", "--label"),
                     ("--c22", "--bad", "lan1"), ("--help", "--label", "lan1"),
                     ("--c22", "--label", "lan1", "extra")):
            self.reads.unlink(missing_ok=True)
            result = self.run_script(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertEqual(result.stdout, "", args)
            self.assertFalse(self.reads.exists(), args)

    def test_invalid_labels_do_not_read(self):
        for label in ("", "-lan1", ".lan1", "_lan1", "../lan1", "lan/1", "lan 1",
                      "lan1\nCASE lan2", "lan1\r", "lan1\t", "lan1;id", "%s",
                      "$(id)", "\u7f51\u53e31"):
            self.reads.unlink(missing_ok=True)
            result = self.run_script("--c22", "--label", label)
            self.assertEqual(result.returncode, 2, repr(label))
            self.assertEqual(result.stdout, "", repr(label))
            self.assertFalse(self.reads.exists(), repr(label))

    def test_label_length_boundary(self):
        self.assertEqual(self.run_script("--c22", "--label", "a" * 48).returncode, 0)
        self.reads.unlink(missing_ok=True)
        result = self.run_script("--c22", "--label", "a" * 49)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.reads.exists())

    def test_help_does_not_read(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("[--label LABEL]", result.stdout)
        self.assertFalse(self.reads.exists())

    def test_missing_interface_still_fails_and_continues(self):
        (self.root / "14f01000.mdio/phy_links").unlink()
        result = self.run_script("--c22", "--label", "lan1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing-interface", result.stderr)
        self.assertIn("=== 14f02000.mdio/phy_links ===", result.stdout)

    def test_read_failure_still_fails(self):
        path = self.root / "14f01000.mdio/phy_links"
        path.unlink()
        path.mkdir()
        result = self.run_script("--c22", "--label", "lan1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error=read-failed", result.stderr)

    def test_driver_error_and_unsupported_id_still_fail(self):
        path = self.root / "14f01000.mdio/phy_links"
        for result_text in ("error=-110", "unsupported-id"):
            path.write_text(HEADER + "\n0a 22 -1 12345678 ---- ---- ---- unknown "
                            + result_text + "\n")
            result = self.run_script("--c22", "--label", "lan1")
            self.assertEqual(result.returncode, 1)

    def test_malformed_table_still_fails(self):
        path = self.root / "14f01000.mdio/phy_links"
        for text in ("", HEADER + "\n", "0a 22 -1 ffffffff ---- ---- ---- unknown no-id\n"):
            path.write_text(text)
            self.assertEqual(self.run_script("--c22", "--label", "lan1").returncode, 1)

    def test_pma_pcs_disagreement_is_not_rewritten(self):
        result = self.run_script("--c45", "--label", "lan1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("05 45 1 001cc849 2058 0002 0006 up ok", result.stdout)
        self.assertIn("05 45 3 001cc849 205c 0582 0582 down ok", result.stdout)


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "docs/port-map-20260918.json").read_text())

    def test_unique_panel_addresses(self):
        ports = self.data["ports"]
        self.assertEqual([p["label"] for p in ports], ["lan1", "lan2", "lan3", "lan4"])
        self.assertEqual([(p["mdio_mmio"], p["phy_addr_hex"]) for p in ports],
                         [("14f02000", "05"), ("14f01000", "0b"),
                          ("14f01000", "0c"), ("14f01000", "0d")])

    def test_one_c22_up_per_labeled_snapshot(self):
        columns = self.data["c22_column_order"]
        for case, values in self.data["c22_stat_now"].items():
            self.assertEqual(len(values), len(columns))
            up = [col for col, value in zip(columns, values) if int(value, 16) & 4]
            expected = []
            if case != "all-unplugged":
                port = next(p for p in self.data["ports"] if p["label"] == case)
                expected = [port["mdio_mmio"] + ":" + port["phy_addr_hex"]]
            self.assertEqual(up, expected)

    def test_unmapped_responder_is_not_assigned(self):
        responder, = self.data["unmapped_responders"]
        self.assertEqual((responder["mdio_mmio"], responder["phy_addr_hex"]),
                         ("14f01000", "0a"))
        self.assertEqual(responder["phy_id"], "84b95032")
        self.assertTrue(all(values[0] == "7949"
                            for values in self.data["c22_stat_now"].values()))

    def test_unknowns_and_c45_difference_are_preserved(self):
        for port in self.data["ports"]:
            for key in ("mac_index", "serdes_mode", "negotiated_speed_mbps"):
                self.assertIsNone(port[key])
        self.assertIsNone(self.data["complete_fit_sha256"])
        self.assertIsNone(self.data["source_firmware_git_commit"])
        c45 = self.data["lan1_c45"]
        self.assertTrue(int(c45["pma_stat_now"], 16) & 4)
        self.assertFalse(int(c45["pcs_stat_now"], 16) & 4)
        self.assertIsNone(c45["cause_of_difference"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
