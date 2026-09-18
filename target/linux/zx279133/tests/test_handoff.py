#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""No compiler, dtc, make, network, MMIO, or live-device access.

Cache tests interpret the actual patched C function with mocked firmware
callbacks. Shell tests redirect fixed paths into a temporary fake sysroot.
DTS/image checks are source contracts, NOT device-tree compilation tests.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'patches-6.12/130-cacheinfo-complete-arch-fallback.patch'
SCRIPT = ROOT / 'base-files/usr/sbin/skd840n-diag'
try:
    from pycparser import c_parser
except ImportError:
    c_parser = None

# Exact function at lines 326-340, gregkh/linux v6.12.103.
# Whole-file Git blob: 89410127089b934a4a6871c88cd1aef67846588d.
ORIGINAL = '''static int cache_setup_properties(unsigned int cpu)
{
\tint ret = 0;

\tif (of_have_populated_dt())
\t\tret = cache_setup_of_node(cpu);
\telse if (!acpi_disabled)
\t\tret = cache_setup_acpi(cpu);

\t// Assume there is no cache information available in DT/ACPI from now.
\tif (ret && use_arch_cache_info())
\t\tuse_arch_info = true;

\treturn ret;
}
'''


def patched_function():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'drivers/base/cacheinfo.c'
        source.parent.mkdir(parents=True)
        source.write_text('\n' * 325 + ORIGINAL)
        run = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i',
                              str(PATCH)], cwd=tmp, capture_output=True,
                             text=True, check=True)
        if re.search(r'offset|fuzz|FAILED', run.stdout + run.stderr):
            raise AssertionError(run.stdout + run.stderr)
        return source.read_text().lstrip('\n')


class Returned(Exception):
    def __init__(self, value):
        self.value = value


def interpret(source, *, dt=True, acpi=False, fw_error=-2, arch=True):
    """Fail on unsupported AST nodes. This is not a C ABI/type checker."""
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    body = c_parser.CParser().parse(text).ext[-1].body
    env = {'cpu': 0, 'acpi_disabled': not acpi, 'true': 1,
           'use_arch_info': False}
    calls = []
    callbacks = {'of_have_populated_dt': dt,
                 'cache_setup_of_node': fw_error,
                 'cache_setup_acpi': fw_error,
                 'use_arch_cache_info': arch}

    def expr(n):
        kind = type(n).__name__
        if kind == 'ID':
            return env[n.name]
        if kind == 'Constant':
            return int(n.value, 0)
        if kind == 'FuncCall':
            name = n.name.name
            calls.append(name)
            return callbacks[name]
        if kind == 'UnaryOp' and n.op == '!':
            return not expr(n.expr)
        if kind == 'BinaryOp' and n.op == '&&':
            return bool(expr(n.left)) and bool(expr(n.right))
        raise AssertionError('unsupported expression: ' + kind)

    def stmt(n):
        if n is None:
            return
        kind = type(n).__name__
        if kind == 'Compound':
            for child in n.block_items or []:
                stmt(child)
        elif kind == 'Decl':
            env[n.name] = expr(n.init)
        elif kind == 'Assignment':
            assert n.op == '=' and type(n.lvalue).__name__ == 'ID'
            env[n.lvalue.name] = expr(n.rvalue)
        elif kind == 'If':
            stmt(n.iftrue if expr(n.cond) else n.iffalse)
        elif kind == 'Return':
            raise Returned(expr(n.expr))
        else:
            raise AssertionError('unsupported statement: ' + kind)

    try:
        stmt(body)
    except Returned as result:
        return result.value, bool(env['use_arch_info']), calls
    raise AssertionError('missing return')


@unittest.skipUnless(c_parser and shutil.which('patch'),
                     'pycparser or patch unavailable: cache tests NOT run')
class CacheFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patched = patched_function()

    def test_reproduces_original_first_cpu_failure(self):
        self.assertEqual(interpret(ORIGINAL)[:2], (-2, True))
        self.assertEqual(interpret(self.patched)[:2], (0, True))

    def test_acpi_fallback(self):
        ret, fallback, calls = interpret(self.patched, dt=False, acpi=True)
        self.assertEqual((ret, fallback), (0, True))
        self.assertIn('cache_setup_acpi', calls)
        self.assertNotIn('cache_setup_of_node', calls)

    def test_no_fallback_preserves_errors(self):
        for dt in (True, False):
            for error in (-2, -12, -22):
                with self.subTest(dt=dt, error=error):
                    self.assertEqual(interpret(self.patched, dt=dt, acpi=not dt,
                        fw_error=error, arch=False)[:2], (error, False))

    def test_success_does_not_select_fallback(self):
        ret, fallback, calls = interpret(self.patched, fw_error=0)
        self.assertEqual((ret, fallback), (0, False))
        self.assertNotIn('use_arch_cache_info', calls)

    def test_without_firmware_keeps_success(self):
        ret, fallback, calls = interpret(self.patched, dt=False, acpi=False)
        self.assertEqual((ret, fallback), (0, False))
        self.assertEqual(calls, ['of_have_populated_dt'])


class SourceContracts(unittest.TestCase):
    def test_mdio_profile_removes_sfc_and_alias(self):
        text = (ROOT / 'dts/zx279133-skyworth-sk-d840n-mdio.dts').read_text()
        self.assertIn('#include "zx279133-skyworth-sk-d840n-io.dts"', text)
        self.assertEqual(text.count('/delete-node/ &spifc;'), 1)
        self.assertEqual(text.count('/delete-property/ spi0;'), 1)
        self.assertNotIn('status = "disabled"', text)
        self.assertNotRegex(text, r'memory@|reserved-memory|bootargs|phy-handle')

    def test_profile_selection_and_fit_contract(self):
        seed = (ROOT / 'docs/build-mdio.config').read_text()
        self.assertIn('CONFIG_TARGET_zx279133_generic_DEVICE_skyworth_sk-d840n-mdio=y', seed)
        for name in ('skyworth_sk-d840n', 'skyworth_sk-d840n-io'):
            self.assertIn('# CONFIG_TARGET_zx279133_generic_DEVICE_' + name +
                          ' is not set', seed)
        self.assertIn('CONFIG_TARGET_INITRAMFS_FORCE=y', seed)
        image = (ROOT / 'image/Makefile').read_text()
        self.assertIn('define Device/skyworth_sk-d840n-mdio\n', image)
        self.assertIn('KERNEL_INITRAMFS =', image)
        self.assertNotIn('KERNEL_INITRAMFS :=', image)
        for line in ('KERNEL_LOADADDR := 0x80000000',
                     'KERNEL_ENTRY := 0x80000000',
                     'DEVICE_DTS_CONFIG := conf@133', 'IMAGES :=\n'):
            self.assertIn(line, image)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sysroot = self.root / 'fake'
        self.log = self.root / 'reads'
        self.log.write_text('')
        text = SCRIPT.read_text()
        for prefix in ('/sys/', '/proc/', '/tmp/sysinfo/'):
            text = text.replace(prefix, str(self.sysroot) + prefix)
        self.script = self.root / 'diag'
        self.script.write_text(text)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        cat = self.bin / 'cat'
        cat.write_text('''#!/bin/sh
case "$1" in "$FIXTURE_ROOT"/*) ;; *) exit 97 ;; esac
printf '%s\n' "$1" >> "$READ_LOG"
exec /bin/cat "$@"
''')
        cat.chmod(0o755)
        for command in ('uname', 'ip', 'dmesg'):
            p = self.bin / command
            p.write_text('#!/bin/sh\nprintf "synthetic test output\\n"\n')
            p.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + ':/usr/bin:/bin',
                        FIXTURE_ROOT=str(self.sysroot), READ_LOG=str(self.log))
        for name, value in {
            '/proc/cmdline': 'console=ttyAMA0 rdinit=/init maxcpus=1\n',
            '/sys/devices/system/cpu/online': '0\n',
            '/sys/devices/system/cpu/cpu0/cache/index0/level': '1\n',
            '/sys/devices/system/cpu/cpu0/cache/index0/type': 'Data\n',
            '/sys/devices/system/cpu/cpu0/cache/index0/shared_cpu_list': '0\n',
        }.items():
            self.write(name, value)
        for bus in ('14f01000', '14f02000'):
            base = '/sys/bus/platform/devices/' + bus + '.mdio/'
            self.write(base + 'phy_ids',
                'candidate clause mmd phy_id result\n02 22 -1 12345678 candidate-id\n')
            self.write(base + 'phy_ids_c45',
                'candidate clause mmd phy_id result\n02 45 1 12345678 candidate-id\n')

    def write(self, name, value):
        path = self.sysroot / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def run_diag(self, *args):
        run = subprocess.run(['sh', str(self.script), *args], env=self.env,
                             capture_output=True, text=True, timeout=10)
        reads = [Path(p).name for p in self.log.read_text().splitlines()
                 if '/14f0' in p]
        return run, reads

    def test_passive_default_and_optional_cache_fields(self):
        run, reads = self.run_diag()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(reads, [])
        self.assertIn('  level=1', run.stdout)
        self.assertNotIn('  size=', run.stdout)

    def test_c22_only(self):
        run, reads = self.run_diag('--mdio-c22')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(reads, ['phy_ids', 'phy_ids'])

    def test_c45_only(self):
        run, reads = self.run_diag('--mdio-c45')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(reads, ['phy_ids_c45', 'phy_ids_c45'])

    def test_legacy_both(self):
        run, reads = self.run_diag('--mdio')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(reads, ['phy_ids', 'phy_ids_c45'] * 2)

    def test_missing_requested_endpoint_fails_but_finishes_report(self):
        p = self.sysroot / 'sys/bus/platform/devices/14f01000.mdio/phy_ids'
        p.unlink()
        run, _ = self.run_diag('--mdio-c22')
        self.assertEqual(run.returncode, 1)
        self.assertIn('requested MDIO endpoint unavailable', run.stdout)
        self.assertIn('Kernel messages', run.stdout)

    def test_reported_transaction_error_is_not_success(self):
        self.write('/sys/bus/platform/devices/14f01000.mdio/phy_ids',
                   '02 22 -1 -------- error=-110\n')
        run, _ = self.run_diag('--mdio-c22')
        self.assertEqual(run.returncode, 1)
        self.assertIn('error=-110', run.stdout)

    def test_invalid_arguments_do_not_collect(self):
        for args in (('--invalid',), ('--mdio', '--mdio-c22')):
            with self.subTest(args=args):
                run, reads = self.run_diag(*args)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, '')
                self.assertEqual(reads, [])
                self.assertIn('Usage:', run.stderr)

    def test_shell_syntax_and_no_write_commands(self):
        subprocess.run(['sh', '-n', str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        self.assertNotIn('ip -br', text)
        self.assertNotRegex(text,
            r'(?m)^\s*(mount|devmem|mtd|flash_erase|nandwrite)\b')


if __name__ == '__main__':
    unittest.main(verbosity=2)
