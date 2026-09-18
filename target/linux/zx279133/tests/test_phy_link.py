#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Interpret isolated C helper ASTs and test the shell in a fake sysroot.

No compiler, preprocessor, kernel, MMIO, PHY or complete C type model runs.
pycparser is optional; skipped AST tests are NOT successful hardware tests.
"""
import operator
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from pycparser import c_parser
except ImportError:
    c_parser = None

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'files-6.12/drivers/misc/skd840n-mdio-diag.c').read_text()
SCRIPT = ROOT / 'base-files/usr/sbin/skd840n-port-map'
HEADER = 'candidate clause mmd phy_id ctrl stat_first stat_now link result'


def function(name):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', SOURCE, flags=re.S)
    match = re.search(r'static (?:int|bool|ssize_t) ' + name + r'\(', text)
    assert match, name
    start = text.index('{', match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


class Return(Exception):
    def __init__(self, value):
        self.value = value


class Pointer:
    def __init__(self, mapping, key):
        self.mapping, self.key = mapping, key

    def get(self):
        return self.mapping[self.key]

    def set(self, value):
        self.mapping[self.key] = value


class Helpers:
    """Deliberately limited interpreter: unhandled AST nodes fail loudly."""
    def __init__(self, values):
        self.values = iter(values)
        self.reads = []
        names = ['skd840n_mdio_link_id', 'skd840n_mdio_get_id',
                 'skd840n_mdio_sample_link']
        text = 'typedef unsigned int u32; typedef unsigned char u8; typedef int bool;\n'
        text += '\n'.join(function(name) for name in names)
        ast = c_parser.CParser().parse(text)
        self.functions = {n.decl.name: n for n in ast.ext
                          if type(n).__name__ == 'FuncDef'}

    def call(self, name, args):
        if name == 'skd840n_mdio_read_reg':
            self.reads.append(tuple(args[1:]))
            return next(self.values)
        func = self.functions[name]
        env = dict(MDIO_MMD_PMAPMD=1, MDIO_MMD_PCS=3, MII_PHYSID1=2,
                   MII_PHYSID2=3, MII_BMCR=0, MII_BMSR=1, BMSR_LSTATUS=4,
                   EOPNOTSUPP=95, ENODATA=61, ESTALE=116)
        env.update(zip([p.name for p in func.decl.type.args.params], args))
        try:
            self.stmt(func.body, env)
        except Return as result:
            return result.value
        raise AssertionError('missing return')

    def expr(self, n, e):
        kind = type(n).__name__
        if kind == 'ID':
            return e[n.name]
        if kind == 'Constant':
            return int(n.value, 0)
        if kind == 'Cast':
            # Inputs are within the represented C types; no overflow model.
            return self.expr(n.expr, e)
        if kind == 'FuncCall':
            return self.call(n.name.name, [self.expr(a, e) for a in n.args.exprs])
        if kind == 'UnaryOp':
            if n.op == '&':
                assert type(n.expr).__name__ == 'ID'
                return Pointer(e, n.expr.name)
            value = self.expr(n.expr, e)
            if n.op == '*':
                return value.get()
            return {'!': lambda x: not x, '-': operator.neg}[n.op](value)
        if kind == 'BinaryOp':
            left = self.expr(n.left, e)
            if n.op == '&&':
                return bool(left) and bool(self.expr(n.right, e))
            if n.op == '||':
                return bool(left) or bool(self.expr(n.right, e))
            ops = {'==': operator.eq, '!=': operator.ne, '<': operator.lt,
                   '&': operator.and_, '|': operator.or_, '<<': operator.lshift}
            return ops[n.op](left, self.expr(n.right, e))
        raise AssertionError('unsupported expression ' + kind)

    def stmt(self, n, e):
        if n is None:
            return
        kind = type(n).__name__
        if kind == 'Compound':
            for child in n.block_items or []:
                self.stmt(child, e)
        elif kind == 'Decl':
            e[n.name] = self.expr(n.init, e) if n.init else None
        elif kind == 'If':
            self.stmt(n.iftrue if self.expr(n.cond, e) else n.iffalse, e)
        elif kind == 'Return':
            raise Return(self.expr(n.expr, e))
        elif kind == 'Assignment':
            assert n.op == '='
            value = self.expr(n.rvalue, e)
            if type(n.lvalue).__name__ == 'ID':
                e[n.lvalue.name] = value
            else:
                assert type(n.lvalue).__name__ == 'UnaryOp' and n.lvalue.op == '*'
                self.expr(n.lvalue.expr, e).set(value)
        else:
            raise AssertionError('unsupported statement ' + kind)

    def sample(self, phy=10, mmd=-1, phy_id=0x84b95032):
        output = dict(ctrl=-1, first=-1, now=-1)
        ret = self.call('skd840n_mdio_sample_link',
                        [None, phy, mmd, phy_id] +
                        [Pointer(output, key) for key in ('ctrl', 'first', 'now')])
        return ret, output


@unittest.skipUnless(c_parser, 'pycparser missing: C AST tests NOT run')
class HelperTests(unittest.TestCase):
    def test_whitelist(self):
        h = Helpers([])
        for phy_id in (0, 0xffffffff, 0x84b95032, 0x001cc849, 0x001cc848):
            for mmd in (-2, -1, 0, 1, 3, 7, 30, 31):
                expected = (mmd == -1 and phy_id in (0x84b95032, 0x001cc849)) or (
                    mmd in (1, 3) and phy_id == 0x001cc849)
                self.assertEqual(h.call('skd840n_mdio_link_id', [phy_id, mmd]), expected)

    def test_unknown_id_never_reads_status(self):
        h = Helpers([])
        self.assertEqual(h.sample(phy_id=0x12345678)[0], -95)
        self.assertEqual(h.reads, [])

    def test_latched_down_then_up_uses_second_read(self):
        h = Helpers([0x1140, 0x7949, 0x794d, 0x84b9, 0x5032])
        ret, data = h.sample()
        self.assertEqual(ret, 1)
        self.assertEqual(data, dict(ctrl=0x1140, first=0x7949, now=0x794d))
        self.assertEqual(h.reads, [(10, -1, r) for r in (0, 1, 1, 2, 3)])

    def test_current_down_not_hidden_by_previous_up(self):
        h = Helpers([0x1140, 0x794d, 0x7949, 0x84b9, 0x5032])
        self.assertEqual(h.sample()[0], 0)

    def test_c45_native_mmd_and_zero_down(self):
        for mmd in (1, 3):
            h = Helpers([0x2040, 0, 0, 0x001c, 0xc849])
            self.assertEqual(h.sample(phy=5, mmd=mmd, phy_id=0x001cc849)[0], 0)
            self.assertEqual(h.reads, [(5, mmd, r) for r in (0, 1, 1, 2, 3)])

    def test_all_ones_never_means_up(self):
        for pos in range(3):
            values = [0x1140, 0x794d, 0x794d]
            values[pos] = 0xffff
            h = Helpers(values)
            self.assertEqual(h.sample()[0], -61)
            self.assertEqual(len(h.reads), 3)

    def test_transport_errors_abort_remaining_reads(self):
        for pos in range(5):
            values = [0x1140, 0x794d, 0x794d, 0x84b9, 0x5032]
            values[pos] = -110
            h = Helpers(values)
            self.assertEqual(h.sample()[0], -110)
            self.assertEqual(len(h.reads), pos + 1)

    def test_changed_id_is_stale_not_up(self):
        h = Helpers([0x1140, 0x794d, 0x794d, 0xffff, 0xffff])
        self.assertEqual(h.sample()[0], -116)


class SourceTests(unittest.TestCase):
    def test_register_guard_and_no_new_phy_writes(self):
        body = function('skd840n_mdio_read_reg')
        self.assertLess(body.index('reg > MII_PHYSID2'), body.index('writel('))
        self.assertIn('mmd != -1', body)
        self.assertIn('mmd != MDIO_MMD_PMAPMD', body)
        self.assertIn('mmd != MDIO_MMD_PCS', body)
        self.assertNotRegex(SOURCE, r'WRITE_DATA|DEVICE_ATTR_RW|module_param|phy_connect')
        probe = function('skd840n_mdio_probe')
        self.assertNotRegex(probe, r'skd840n_mdio_(read_reg|sample_link|transfer)\(')

    def test_bus_lock_and_bounded_table(self):
        body = function('skd840n_mdio_links')
        self.assertLess(body.index('mutex_lock('), body.index('skd840n_mdio_get_id('))
        self.assertGreater(body.index('mutex_unlock('), body.index('skd840n_mdio_sample_link('))
        self.assertIn('{ 2, 5, 10, 11, 12, 13, 28 }', SOURCE)
        # 14 rows, conservatively bounded at 128 bytes per row, 4 KiB pages.
        self.assertLess(len(HEADER) + 1 + 14 * 128, 4096)
        self.assertIn('DEVICE_ATTR_RO(phy_links);', SOURCE)
        self.assertIn('DEVICE_ATTR_RO(phy_links_c45);', SOURCE)


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / 'port-map'
        self.script.write_text(SCRIPT.read_text().replace(
            'base=/sys/bus/platform/devices', 'base=' + str(self.root)))
        for dev in ('14f01000.mdio', '14f02000.mdio'):
            d = self.root / dev
            d.mkdir()
            (d / 'phy_links').write_text(HEADER + '\n0a 22 -1 84b95032 1140 7949 794d up ok\n')
            (d / 'phy_links_c45').write_text(HEADER + '\n05 45 1 001cc849 2040 0000 0004 up ok\n')

    def run_script(self, *args):
        return subprocess.run(['sh', str(self.script), *args], capture_output=True, text=True)

    def test_syntax_and_required_explicit_mode(self):
        subprocess.run(['sh', '-n', str(SCRIPT)], check=True)
        for args in ((), ('--invalid',), ('--c22', 'extra')):
            r = self.run_script(*args)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn('snapshot', r.stdout)

    def test_help_does_not_read(self):
        self.assertEqual(self.run_script('--help').returncode, 0)
        self.assertNotIn('84b95032', self.run_script('--help').stdout)

    def test_c22_separate(self):
        r = self.run_script('--c22')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('84b95032', r.stdout)
        self.assertNotIn('001cc849', r.stdout)

    def test_c45_separate(self):
        r = self.run_script('--c45')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('001cc849', r.stdout)
        self.assertNotIn('84b95032', r.stdout)

    def test_missing_interface_fails_but_continues(self):
        (self.root / '14f01000.mdio/phy_links').unlink()
        r = self.run_script('--c22')
        self.assertEqual(r.returncode, 1)
        self.assertIn('missing-interface', r.stderr)
        self.assertIn('14f02000.mdio', r.stdout)

    def test_reported_errors_fail(self):
        for row in ('0a 22 -1 84b95032 ---- ---- ---- unknown error=-110',
                    '0a 22 -1 12345678 ---- ---- ---- unknown unsupported-id'):
            (self.root / '14f01000.mdio/phy_links').write_text(HEADER + '\n' + row + '\n')
            self.assertEqual(self.run_script('--c22').returncode, 1)

    def test_all_ones_id_is_not_a_transaction_error(self):
        row = HEADER + '\n02 22 -1 ffffffff ---- ---- ---- unknown no-id\n'
        (self.root / '14f01000.mdio/phy_links').write_text(row)
        self.assertEqual(self.run_script('--c22').returncode, 0)

    def test_malformed_table_fails(self):
        for text in ('', HEADER + '\n'):
            (self.root / '14f01000.mdio/phy_links').write_text(text)
            self.assertEqual(self.run_script('--c22').returncode, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
