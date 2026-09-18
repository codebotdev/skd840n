#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Static contracts and interpreted command-policy tests; NEVER compile C.

The optional pycparser tests evaluate the actual isolated C policy AST, with
nonnegative, in-range scalar inputs and Python integers. They do NOT model
compiler types/overflow, SPI core, MMIO, interrupts, timing or real hardware.
Install pycparser on the development host to run those tests; not on the router.
"""
import operator
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SFC = (ROOT / 'files-6.12/drivers/spi/spi-skd840n-ro.c').read_text()
MDIO = (ROOT / 'files-6.12/drivers/misc/skd840n-mdio-diag.c').read_text()
DTS = (ROOT / 'dts/zx279133-skyworth-sk-d840n-io.dts').read_text()
try:
    from pycparser import c_parser
except ImportError:
    c_parser = None


def uncomment(text):
    return re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)


def function(text, name):
    """Extract a definition, including its opening declaration."""
    start = text.index('static int ' + name + '(')
    pos = text.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


class Returned(Exception):
    def __init__(self, value):
        self.value = value


class Policy:
    """Fail on every unimplemented AST node, rather than guess C semantics."""
    def __init__(self):
        source = function(uncomment(SFC), 'skd840n_sfc_readonly_op')
        prefix = 'typedef unsigned char u8; typedef int bool;\n'
        self.body = c_parser.CParser().parse(prefix + source).ext[-1].body

    def expr(self, node, env):
        name = type(node).__name__
        if name == 'Constant':
            return int(node.value, 0)
        if name == 'ID':
            return env[node.name]
        if name == 'StructRef':
            return self.expr(node.name, env)[node.field.name]
        if name == 'Cast':
            return self.expr(node.expr, env)
        if name == 'UnaryOp':
            value = self.expr(node.expr, env)
            if node.op == '*':
                return value[0]
            return {'!': lambda a: not a, '~': operator.invert,
                    '-': operator.neg}[node.op](value)
        if name == 'BinaryOp':
            left = self.expr(node.left, env)
            if node.op == '&&':
                return bool(left) and bool(self.expr(node.right, env))
            if node.op == '||':
                return bool(left) or bool(self.expr(node.right, env))
            right = self.expr(node.right, env)
            operations = {'==': operator.eq, '!=': operator.ne,
                          '<': operator.lt, '<=': operator.le,
                          '>': operator.gt, '>=': operator.ge,
                          '&': operator.and_, '-': operator.sub}
            return operations[node.op](left, right)
        if name == 'TernaryOp':
            return self.expr(node.iftrue if self.expr(node.cond, env)
                             else node.iffalse, env)
        raise AssertionError('unimplemented expression: ' + name)

    def stmt(self, node, env):
        if node is None:
            return
        name = type(node).__name__
        if name == 'Compound':
            for child in node.block_items or []:
                self.stmt(child, env)
        elif name == 'Return':
            raise Returned(self.expr(node.expr, env))
        elif name == 'Decl':
            env[node.name] = self.expr(node.init, env) if node.init else None
        elif name == 'Assignment':
            assert node.op == '=' and type(node.lvalue).__name__ == 'ID'
            env[node.lvalue.name] = self.expr(node.rvalue, env)
        elif name == 'If':
            self.stmt(node.iftrue if self.expr(node.cond, env)
                      else node.iffalse, env)
        elif name == 'Switch':
            value = self.expr(node.cond, env)
            active = False
            for label in node.stmt.block_items:
                kind = type(label).__name__
                assert kind in ('Case', 'Default'), kind
                if kind == 'Default' or self.expr(label.expr, env) == value:
                    active = True
                if active:
                    for child in label.stmts or []:
                        self.stmt(child, env)
        else:
            raise AssertionError('unimplemented statement: ' + name)

    def run(self, opcode, *, known=True, addr_bytes=0, addr=0, dummy=0,
            direction=0, size=0, value=0):
        env = {'known': known, 'EROFS': 30, 'ENODEV': 19,
               'SPI_MEM_NO_DATA': 0, 'SPI_MEM_DATA_IN': 1,
               'SPI_MEM_DATA_OUT': 2, 'SKD840N_PAGE_WITH_OOB': 2176,
               'op': {'cmd': {'opcode': opcode},
                      'addr': {'nbytes': addr_bytes, 'val': addr},
                      'dummy': {'nbytes': dummy},
                      'data': {'dir': direction, 'nbytes': size,
                               'buf': {'out': [value]}}}}
        try:
            self.stmt(self.body, env)
        except Returned as result:
            return result.value
        raise AssertionError('policy fell through without a return')


@unittest.skipUnless(c_parser, 'pycparser unavailable: AST tests NOT run')
class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = Policy()

    def test_all_unlisted_opcodes_rejected(self):
        allowed = {0xff, 0x9f, 0x0f, 0x1f, 0x13, 0x03, 0x0b}
        for opcode in set(range(256)) - allowed:
            for known in (True, False):
                for direction in (0, 1, 2):
                    with self.subTest(opcode=opcode, known=known, dir=direction):
                        self.assertLess(self.policy.run(opcode, known=known,
                            direction=direction, size=1), 0)

    def test_reset_and_readid(self):
        self.assertEqual(self.policy.run(0xff, known=False), 0)
        self.assertLess(self.policy.run(0xff, size=1), 0)
        self.assertEqual(self.policy.run(0x9f, known=False, dummy=1,
                                        direction=1, size=4), 0)
        for size in (0, 1, 2, 9, 4096):
            self.assertLess(self.policy.run(0x9f, direction=1, size=size), 0)
        self.assertLess(self.policy.run(0x9f, direction=2, size=4), 0)

    def test_get_feature(self):
        for addr in (0, 0x30, 0xa0, 0xb0, 0xc0, 0xff):
            self.assertEqual(self.policy.run(0x0f, known=False, addr_bytes=1,
                addr=addr, direction=1, size=1), 0)
        self.assertLess(self.policy.run(0x0f, addr_bytes=1, addr=0x100,
            direction=1, size=1), 0)

    def test_feature_whitelist_and_unknown_chip(self):
        for addr in range(256):
            for value in range(256):
                accepted = (addr == 0xa0 and value == 0) or (
                    addr == 0xb0 and value & ~0x18 == 0)
                result = self.policy.run(0x1f, addr_bytes=1, addr=addr,
                                        direction=2, size=1, value=value)
                self.assertEqual(result == 0, accepted, (addr, value))
        self.assertLess(self.policy.run(0x1f, known=False, addr_bytes=1,
            addr=0xa0, direction=2, size=1), 0)
        self.assertLess(self.policy.run(0x1f, addr_bytes=1,
            addr=0xb0, direction=2, size=2), 0)

    def test_row_and_cache_bounds(self):
        self.assertEqual(self.policy.run(0x13, addr_bytes=3, addr=131071), 0)
        self.assertLess(self.policy.run(0x13, addr_bytes=3, addr=131072), 0)
        self.assertLess(self.policy.run(0x13, known=False, addr_bytes=3), 0)
        for opcode in (0x03, 0x0b):
            self.assertEqual(self.policy.run(opcode, addr_bytes=2, dummy=1,
                                            direction=1, size=2176), 0)
            self.assertEqual(self.policy.run(opcode, addr_bytes=2, addr=2175,
                                            direction=1, size=1), 0)
            self.assertLess(self.policy.run(opcode, addr_bytes=2, addr=2175,
                                           direction=1, size=2), 0)
            self.assertLess(self.policy.run(opcode, addr_bytes=2,
                                           direction=2, size=1), 0)
            self.assertLess(self.policy.run(opcode, known=False, addr_bytes=2,
                                           direction=1, size=1), 0)


class StaticContracts(unittest.TestCase):
    def test_gate_precedes_hardware_access(self):
        body = function(uncomment(SFC), 'zx_sfc_exec_op')
        gate = body.index('skd840n_sfc_readonly_op(')
        self.assertLess(gate, body.index('zx_sfc_wait_idle('))
        self.assertLess(gate, body.index('regmap_update_bits('))
        self.assertLess(gate, body.index('writel('))
        self.assertRegex(body[gate:], r'if \(ret\)\s+goto out_unlock;')
        self.assertNotRegex(uncomment(SFC), r'->transfer\w*\s*=|module_param')
        self.assertIn('id[0] == 0xef && id[1] == 0xaa && id[2] == 0x22', SFC)

    def test_mdio_has_no_write_operation_or_probe_transactions(self):
        text = uncomment(MDIO)
        self.assertNotIn('WRITE_DATA', text)
        self.assertNotIn('MDIO_OP_WRITE', text)
        self.assertNotIn('DEVICE_ATTR_RW', text)
        self.assertNotIn('skd840n_mdio_transfer(',
                         function(text, 'skd840n_mdio_probe'))
        self.assertIn('{ 2, 5, 10, 11, 12, 13, 28 }', text)
        self.assertNotRegex(text, r'gpio|mdiobus_register|phy_connect')

    def test_isolated_profile_and_protection(self):
        text = uncomment(DTS)
        self.assertIn('#include "zx279133-skyworth-sk-d840n.dts"', text)
        self.assertEqual(text.count('partition@'), 1)
        self.assertIn('read-only;', text)
        self.assertIn('reg = <0 0x10000000>;', text)
        self.assertNotRegex(text, r'ethernet-phy|reset-gpios|dma-coherent')
        image = (ROOT / 'image/Makefile').read_text()
        self.assertIn('KERNEL_INITRAMFS =', image)
        self.assertNotIn('KERNEL_INITRAMFS :=', image)
        for name in ('skyworth_sk-d840n', 'skyworth_sk-d840n-io'):
            self.assertIn('define Device/' + name + '\n', image)
        config = (ROOT / 'config-6.12').read_text()
        symbols = re.findall(r'^(?:# )?(CONFIG_\w+)(?:=| is not set)',
                             config, re.M)
        self.assertEqual(len(symbols), len(set(symbols)))
        for opt in ('SPI_SPIDEV', 'MTD_BLOCK', 'MTD_UBI', 'MTD_CMDLINE_PARTS'):
            self.assertIn('# CONFIG_' + opt + ' is not set', config)

    def test_shell_syntax_and_argument_rejection(self):
        script = ROOT / 'base-files/usr/sbin/skd840n-diag'
        subprocess.run(['sh', '-n', str(script)], check=True)
        bad = subprocess.run(['sh', str(script), '--invalid'],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn('Usage:', bad.stderr)
        self.assertEqual(bad.stdout, '')
        text = uncomment(script.read_text())
        self.assertNotIn('ip -br', text)
        self.assertNotRegex(text, r'(?m)^\s*(mount|devmem|mtd|flash_erase|nandwrite)\b')


if __name__ == '__main__':
    unittest.main(verbosity=2)
