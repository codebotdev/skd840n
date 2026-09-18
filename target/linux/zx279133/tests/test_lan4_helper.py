#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline helper regressions; fake sysfs and a strict BusyBox-shaped ip stub.

Run with sh (default) or LAN4_TEST_SHELL='busybox ash'. No real interface,
network, register, compiler or firmware is accessed. The ip stub deliberately
rejects unsupported/unexpected arguments instead of accepting every command.
"""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get('LAN4_HELPER_SOURCE', str(ROOT / 'base-files/usr/sbin/skd840n-lan4-test')))
COUNTERS = ('rx_packets', 'rx_bytes', 'rx_errors', 'rx_dropped',
            'tx_packets', 'tx_bytes', 'tx_errors', 'tx_dropped')
STATE = ('version=lan4-direct-recovery-v1 interface=lan4test stage=direct-ready running=1 error=0 link_error=0\n'
         'attempted=1 dma_retained=1 carrier=1 packet_limit=512\n'
         'wait_offset=0x0 wait_value=0x0 irq=0 rx_consumed=0 rx_delivered=0 rx_drop=0\n'
         'tx_submitted=13 tx_completed=13 tx_pending=0 tx_drop=0\n')


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.write_text(STATE)
        self.stats = self.root / 'statistics'
        self.stats.mkdir()
        for key in COUNTERS:
            (self.stats / key).write_text('13\n' if key == 'tx_packets' else '0\n')
        self.calls = self.root / 'ip-calls'
        self.script = self.root / 'helper'
        self.script.write_text(SCRIPT.read_text().replace(
            '/sys/class/net/lan4test/device/test_state', str(self.state)).replace(
            '/sys/class/net/lan4test/statistics', str(self.stats)))
        (self.root / 'ip').write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$CALLS"
[ "$*" != "${FAIL_IP:-never}" ] || exit 1
case "$*" in
    "link show dev lan4test") echo '2: lan4test: <BROADCAST,MULTICAST,UP,LOWER_UP>' ;;
    "-f inet addr show dev lan4test")
        [ "${HAS_ADDRESS:-0}" = 1 ] && echo '    inet 192.168.1.1/24 scope global lan4test'
        exit 0 ;;
    "-f inet route list dev lan4test") echo '192.168.1.0/24 dev lan4test scope link src 192.168.1.1' ;;
    "neigh show dev lan4test") echo '192.168.1.101 dev lan4test FAILED' ;;
    "link set dev lan4test up"|"link set dev lan4test down"|"addr add 192.168.1.1/24 dev lan4test") ;;
    *) echo "unsupported ip arguments: $*" >&2; exit 64 ;;
esac
''')
        (self.root / 'id').write_text('#!/bin/sh\necho "${FAKE_UID:-0}"\n')
        for name in ('ip', 'id'):
            (self.root / name).chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root) + ':/usr/bin:/bin',
                    'CALLS': str(self.calls)}
        self.shell = shlex.split(os.environ.get('LAN4_TEST_SHELL', 'sh'))

    def run_helper(self, *args):
        return subprocess.run([*self.shell, str(self.script), *args],
                              env=self.env, capture_output=True, text=True, timeout=5)

    def call_lines(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def fresh_boot(self):
        self.state.write_text(STATE.replace('attempted=1', 'attempted=0').replace(
            'stage=direct-ready running=1', 'stage=registered-down running=0').replace(
            'dma_retained=1 carrier=1', 'dma_retained=0 carrier=0'))

    def test_status_on_rx_zero_session_is_passive_and_complete(self):
        self.env['FAKE_UID'] = '1000'
        result = self.run_helper('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('tx_submitted=13 tx_completed=13', result.stdout)
        self.assertIn('tx_packets=13', result.stdout)
        self.assertIn('rx_packets=0', result.stdout)
        self.assertIn('192.168.1.101 dev lan4test FAILED', result.stdout)
        self.assertIn('not connectivity', result.stdout)
        self.assertEqual(self.call_lines(), ['link show dev lan4test',
            '-f inet addr show dev lan4test', '-f inet route list dev lan4test',
            'neigh show dev lan4test'])

    def test_missing_counter_does_not_hide_neighbours(self):
        (self.stats / 'rx_bytes').unlink()
        result = self.run_helper('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('rx_bytes=unavailable', result.stdout)
        self.assertIn('tx_packets=13', result.stdout)
        self.assertIn('FAILED', result.stdout)

    def test_bad_counter_never_becomes_zero(self):
        for value in ('', '-1', 'not-a-count', '12 34'):
            with self.subTest(value=value):
                (self.stats / 'rx_packets').write_text(value)
                result = self.run_helper('status')
                self.assertEqual(result.returncode, 1)
                self.assertIn('rx_packets=invalid', result.stdout)
                self.assertIn('FAILED', result.stdout)

    def test_large_counter_preserved_without_shell_arithmetic(self):
        value = '18446744073709551615'
        (self.stats / 'rx_bytes').write_text(value + '\n')
        result = self.run_helper('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('rx_bytes=' + value, result.stdout)

    def test_failed_link_query_does_not_hide_address_or_neighbours(self):
        self.env.update(FAIL_IP='link show dev lan4test', HAS_ADDRESS='1')
        result = self.run_helper('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('inet 192.168.1.1/24', result.stdout)
        self.assertIn('FAILED', result.stdout)

    def test_driver_errors_are_reported_without_truncation(self):
        for code in ('-110', '-122', '7'):
            with self.subTest(code=code):
                self.state.write_text(STATE.replace(' error=0 ', ' error=' + code + ' '))
                result = self.run_helper('status')
                self.assertEqual(result.returncode, 1)
                self.assertIn('tx_packets=13', result.stdout)
                self.assertIn('FAILED', result.stdout)

    def test_missing_error_field_is_not_success(self):
        self.state.write_text(STATE.replace(' error=0 ', ' '))
        self.assertEqual(self.run_helper('status').returncode, 1)

    def test_missing_state_has_no_ip_side_effect(self):
        self.state.unlink()
        self.assertEqual(self.run_helper('status').returncode, 1)
        self.assertEqual(self.call_lines(), [])

    def test_bad_version_has_no_ip_side_effect(self):
        self.state.write_text(STATE.replace('recovery-v1', 'unknown'))
        self.assertEqual(self.run_helper('start').returncode, 1)
        self.assertEqual(self.call_lines(), [])

    def test_repeat_activation_is_rejected(self):
        result = self.run_helper('start')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Already activated', result.stderr)
        self.assertEqual(self.call_lines(), [])

    def test_start_preflight_before_up_and_assign_once(self):
        self.fresh_boot()
        result = self.run_helper('start')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.call_lines()
        self.assertEqual(calls[:3], ['-f inet addr show dev lan4test',
            'link set dev lan4test up', 'addr add 192.168.1.1/24 dev lan4test'])
        self.assertEqual(calls.count('link set dev lan4test up'), 1)

    def test_existing_address_not_added_again(self):
        self.fresh_boot()
        self.env['HAS_ADDRESS'] = '1'
        self.assertEqual(self.run_helper('start').returncode, 0)
        self.assertNotIn('addr add 192.168.1.1/24 dev lan4test', self.call_lines())

    def test_preflight_failure_never_activates(self):
        self.fresh_boot()
        self.env['FAIL_IP'] = '-f inet addr show dev lan4test'
        self.assertEqual(self.run_helper('start').returncode, 1)
        self.assertEqual(self.call_lines(), ['-f inet addr show dev lan4test'])

    def test_failed_up_has_no_address_assignment(self):
        self.fresh_boot()
        self.env['FAIL_IP'] = 'link set dev lan4test up'
        result = self.run_helper('start')
        self.assertEqual(result.returncode, 1)
        self.assertIn('do not retry', result.stderr)
        self.assertNotIn('addr add 192.168.1.1/24 dev lan4test', self.call_lines())

    def test_address_failure_does_not_repeat_up(self):
        self.fresh_boot()
        self.env['FAIL_IP'] = 'addr add 192.168.1.1/24 dev lan4test'
        result = self.run_helper('start')
        self.assertEqual(result.returncode, 1)
        self.assertIn('after activation', result.stderr)
        self.assertEqual(self.call_lines().count('link set dev lan4test up'), 1)

    def test_stop_only_on_explicit_request(self):
        result = self.run_helper('stop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DMA memory retained', result.stdout)
        self.assertEqual(self.call_lines()[0], 'link set dev lan4test down')
        self.assertNotIn('link set dev lan4test up', self.call_lines())

    def test_root_required_for_start(self):
        self.fresh_boot()
        self.env['FAKE_UID'] = '1000'
        self.assertEqual(self.run_helper('start').returncode, 1)
        self.assertEqual(self.call_lines(), [])

    def test_help_bad_arguments_and_syntax(self):
        self.assertEqual(subprocess.run([*self.shell, '-n', str(self.script)]).returncode, 0)
        self.assertEqual(self.run_helper('--help').returncode, 0)
        for args in ((), ('bogus',), ('status', 'extra')):
            self.assertEqual(self.run_helper(*args).returncode, 2)
        self.assertEqual(self.call_lines(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
