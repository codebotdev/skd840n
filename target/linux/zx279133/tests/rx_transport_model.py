# SPDX-License-Identifier: GPL-2.0-only
"""Finite interpreter of selected, actual RX C functions for fault injection.

No compiler, preprocessor, kernel, devices or network are executed. Models
single-owner control flow and explicitly mocked DMA/MMIO calls. Allocation,
hash-list implementation, C ABI/aliasing, hardware ordering and concurrency are
NOT modeled. min_t(u32, ...) is translated to one explicit model intrinsic.
Unknown syntax fails rather than silently succeeding.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from pycparser import c_ast as A, c_parser

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / 'files-6.12/drivers/net/ethernet/zte'
sys.path.insert(0, str(ROOT / 'tools'))
from idm_core import load_spec, expression


def function(source, name):
    match = re.search(r'^(?:static\s+)?(?:inline\s+)?(?:int|void)\s+' +
                      re.escape(name) + r'\s*\(', source, re.M)
    if not match:
        raise ValueError('function not found: ' + name)
    start = source.index('{', match.start())
    level = 1
    end = start + 1
    while level:
        level += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


class Ref:
    def __init__(self, mapping, key, kind=None):
        self.mapping, self.key, self.kind = mapping, key, kind

    def get(self):
        return self.mapping[self.key]

    def set(self, value):
        if self.kind in ('u8', 'u16', 'u32', 'u64'):
            value &= (1 << int(self.kind[1:])) - 1
        elif self.kind == 'bool':
            value = int(bool(value))
        self.mapping[self.key] = value


@dataclass
class Ptr:
    memory: object
    index: int = 0
    label: str = 'memory'

    def at(self, index=0):
        index += self.index
        if not 0 <= index < len(self.memory):
            raise IndexError('out-of-bounds ' + self.label)
        return Ref(self.memory, index)

    def __add__(self, count):
        return Ptr(self.memory, self.index + count, self.label)


class Return(Exception):
    pass


class Break(Exception):
    pass


class Continue(Exception):
    pass


def truth(value):
    # A pointer to a zero-filled object is still non-null.
    return value is not None and (not isinstance(value, int) or value != 0)


class Interpreter:
    def __init__(self, hooks):
        self.hooks = hooks
        self.constants = load_spec()['constants']
        maths = (DRIVERS / 'skd840n-idm-ring-math.h').read_text()
        for name, value in re.findall(r'^#define (SKD840N_\w+)\s+([^\n]+)$', maths, re.M):
            self.constants[name] = expression(value, self.constants)
        self.constants.update(NULL=None, true=1, false=0, DMA_FROM_DEVICE=2,
                              EINVAL=22, ERANGE=34, EMSGSIZE=90, EOVERFLOW=75,
                              EAGAIN=11, EUCLEAN=117, SKD840N_RX_BUFFERS=4224)
        rx = (DRIVERS / 'skd840n-idm-rx.c').read_text()
        names = ['skd840n_rx_fault', 'skd840n_rx_release', 'skd840n_rx_one',
                 'skd840n_idm_rx_poll', 'skd840n_idm_rx_destroy',
                 'skd840n_idm_rx_publish']
        source = '\n'.join(function(rx, name) for name in names) + '\n' + maths
        source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
        source = re.sub(r'^#.*$', '', source, flags=re.M)
        source = source.replace('min_t(u32,', 'model_min(')
        prefix = ('typedef unsigned int u32; typedef unsigned short u16; '
                  'typedef unsigned char u8; typedef unsigned long long u64; '
                  'typedef unsigned int __le32; typedef unsigned int __be32; '
                  'typedef int bool;\n')
        tree = c_parser.CParser().parse(prefix + source)
        self.functions = {n.decl.name: n for n in tree.ext if isinstance(n, A.FuncDef)}
        self.steps = 0

    def call(self, name, *args):
        if name in self.hooks:
            return self.hooks[name](*args)
        node = self.functions[name]
        params = node.decl.type.args.params
        if len(params) != len(args):
            raise ValueError('argument count: ' + name)
        env = dict(self.constants)
        env.update((param.name, arg) for param, arg in zip(params, args))
        try:
            self.stmt(node.body, env)
        except Return as r:
            return r.args[0]
        return None

    def ref(self, node, env):
        if isinstance(node, A.ID):
            return Ref(env, node.name)
        if isinstance(node, A.StructRef):
            owner = self.expr(node.name, env)
            if owner is None:
                raise ValueError('null member access')
            return Ref(owner, node.field.name)
        if isinstance(node, A.ArrayRef):
            p = self.expr(node.name, env)
            return p.at(self.expr(node.subscript, env))
        if isinstance(node, A.UnaryOp) and node.op == '*':
            p = self.expr(node.expr, env)
            return p if isinstance(p, Ref) else p.at()
        raise ValueError('unsupported lvalue: ' + type(node).__name__)

    def expr(self, node, env):
        if isinstance(node, (A.ID, A.StructRef, A.ArrayRef)):
            return self.ref(node, env).get()
        if isinstance(node, A.Constant):
            return int(re.sub('[uUlL]+$', '', node.value), 0)
        if isinstance(node, A.Cast):
            return self.expr(node.expr, env)
        if isinstance(node, A.UnaryOp):
            if node.op == '&':
                return self.ref(node.expr, env)
            if node.op == '*':
                return self.ref(node, env).get()
            value = self.expr(node.expr, env)
            if node.op == '!':
                return int(not truth(value))
            if node.op == '-':
                return -value
            if node.op == '~':
                return ~value
            if node.op in ('p++', '++'):
                self.ref(node.expr, env).set(value + 1)
                return value if node.op == 'p++' else value + 1
            raise ValueError('unsupported unary: ' + node.op)
        if isinstance(node, A.BinaryOp):
            a = self.expr(node.left, env)
            if node.op == '&&':
                return int(truth(a) and truth(self.expr(node.right, env)))
            if node.op == '||':
                return int(truth(a) or truth(self.expr(node.right, env)))
            b = self.expr(node.right, env)
            operations = {'+': lambda: a + b, '-': lambda: a - b,
                          '*': lambda: a * b, '%': lambda: a % b,
                          '&': lambda: a & b, '|': lambda: a | b,
                          '>>': lambda: a >> b, '<<': lambda: a << b,
                          '<': lambda: int(a < b), '>': lambda: int(a > b),
                          '<=': lambda: int(a <= b), '>=': lambda: int(a >= b),
                          '==': lambda: int(a == b), '!=': lambda: int(a != b)}
            return operations[node.op]()
        if isinstance(node, A.Assignment):
            ref = self.ref(node.lvalue, env)
            value = self.expr(node.rvalue, env)
            if node.op == '+=':
                value = ref.get() + value
            elif node.op == '-=':
                value = ref.get() - value
            elif node.op != '=':
                raise ValueError('unsupported assignment: ' + node.op)
            ref.set(value)
            return value
        if isinstance(node, A.FuncCall):
            args = node.args.exprs if node.args else []
            if isinstance(node.name, A.ID) and node.name.name == 'WRITE_ONCE':
                ref = self.ref(args[0], env)
                value = self.expr(args[1], env)
                self.hooks['write_once'](ref, value)
                ref.set(value)
                return value
            args = [self.expr(x, env) for x in args]
            if isinstance(node.name, A.StructRef):
                return self.expr(node.name, env)(*args)
            return self.call(node.name.name, *args)
        raise ValueError('unsupported expression: ' + type(node).__name__)

    def stmt(self, node, env):
        self.steps += 1
        if self.steps > 200000:
            raise RuntimeError('model instruction limit')
        if node is None:
            return
        if isinstance(node, A.Compound):
            for child in node.block_items or []:
                self.stmt(child, env)
        elif isinstance(node, A.Decl):
            if isinstance(node.type, A.ArrayDecl):
                env[node.name] = Ptr([0] * self.expr(node.type.dim, env))
            else:
                env[node.name] = self.expr(node.init, env) if node.init else None
        elif isinstance(node, A.DeclList):
            for decl in node.decls:
                self.stmt(decl, env)
        elif isinstance(node, A.If):
            self.stmt(node.iftrue if truth(self.expr(node.cond, env)) else node.iffalse, env)
        elif isinstance(node, (A.For, A.While)):
            if isinstance(node, A.For):
                self.stmt(node.init, env)
            while truth(self.expr(node.cond, env)):
                try:
                    self.stmt(node.stmt, env)
                except Break:
                    break
                except Continue:
                    pass
                if isinstance(node, A.For):
                    self.expr(node.next, env)
        elif isinstance(node, A.Break):
            raise Break()
        elif isinstance(node, A.Continue):
            raise Continue()
        elif isinstance(node, A.Return):
            raise Return(self.expr(node.expr, env) if node.expr else None)
        elif isinstance(node, (A.Assignment, A.UnaryOp, A.FuncCall)):
            self.expr(node, env)
        else:
            raise ValueError('unsupported statement: ' + type(node).__name__)


class Hardware:
    """Immediate MMIO-command acceptance model, not a simulation of the SoC."""
    def __init__(self):
        self.trace, self.received, self.counts = [], [], [0] * 24
        self.busy, self.stop_error, self.receive_error = False, 0, 0
        self.by_dma = {}
        self.mask = 0
        stats = {x: 0 for x in ('consumed', 'delivered', 'dropped', 'release_busy', 'not_ready', 'faults')}
        self.rx = dict(dev='dev', idm=0, priv=None,
                       ops=dict(receive=self.receive, quiesce=self.quiesce),
                       descriptors=Ptr([0] * (24 * 1024 * 8), label='desc'),
                       normal_bp=Ptr([0] * 8192, label='normal_bp'),
                       jumbo_bp=Ptr([0] * 4096, label='jumbo_bp'),
                       pending=Ptr([None] * 24), consumer=Ptr([0] * 24),
                       normal_producer=4096, jumbo_producer=128, cursor=0,
                       exposed=True, running=True, stopping=False, fault=0, stats=stats)
        hooks = dict(readl=self.readl, writel=self.writel, write_once=self.write_once,
                     READ_ONCE=lambda x: x, le32_to_cpu=lambda x: x,
                     cpu_to_le32=lambda x: x, lower_32_bits=lambda x: x & 0xffffffff,
                     cpu_to_be32=lambda x: int.from_bytes(x.to_bytes(4, 'little'), 'big'),
                     BIT=lambda x: 1 << x, model_min=min,
                     dma_wmb=lambda: self.trace.append(('wmb',)),
                     dma_rmb=lambda: self.trace.append(('rmb',)),
                     dma_sync_single_for_cpu=lambda *args: self.trace.append(('sync_cpu', args[1])),
                     dma_sync_single_for_device=lambda *args: self.trace.append(('sync_device', args[1])),
                     skd840n_rx_lookup=lambda rx, dma: self.by_dma.get(dma),
                     skd840n_rx_free=lambda rx: self.trace.append(('free',)))
        self.model = Interpreter(hooks)

    def readl(self, reg):
        self.trace.append(('read', reg))
        if reg == 0x88:
            return (1 << 31) if self.busy else 0
        if reg == 0x40:
            return self.mask
        if 0xc4 <= reg <= 0xf0 and not reg % 4:
            q = (reg - 0xc4) // 4 * 2
            return self.counts[q] | (self.counts[q + 1] << 16)
        raise ValueError('unexpected register read')

    def writel(self, word, reg):
        self.trace.append(('write', reg, word))
        if reg == 0x88:
            if self.busy:
                raise AssertionError('command written while busy')
            q = (word >> 12) & 31
            count = word & 0xfff
            if count > self.counts[q]:
                raise AssertionError('released unowned descriptor')
            self.counts[q] -= count
        elif reg == 0x40:
            self.mask = word
        elif reg != 0x100:
            raise ValueError('unexpected register write')

    def write_once(self, ref, word):
        self.trace.append(('memory_write', ref.key, word))

    def receive(self, priv, data, length, words, queue):
        self.trace.append(('deliver', queue))
        self.received.append((queue, bytes(data.memory[data.index:data.index + length]), words.memory[:]))
        return self.receive_error

    def quiesce(self, priv):
        self.trace.append(('quiesce', self.stop_error))
        return self.stop_error

    def frame(self, queue, slot=0, dma=None, flags=None, jumbo=False, length=98):
        dma = dma if dma is not None else 0x88000000 + len(self.by_dma) * 0x4000
        memory = bytearray([0x55] * 128 + [0xab] * (16128 - 128 if jumbo else 2304 - 128))
        b = dict(cpu=Ptr(memory, label='payload'), dma=dma, posted=True,
                 bytes=len(memory), jumbo=jumbo)
        self.by_dma[dma] = b
        idx = (queue * 1024 + slot) * 8
        words = self.rx['descriptors'].memory
        words[idx] = dma
        words[idx + 1] = flags if flags is not None else (length | (3 << 16) | (0x4000 if jumbo else 0))
        self.counts[queue] += 1
        return b

    def poll(self, budget):
        more = [False]
        self.model.steps = 0
        result = self.model.call('skd840n_idm_rx_poll', self.rx, budget, Ptr(more))
        return result, bool(more[0])
