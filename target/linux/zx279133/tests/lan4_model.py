# SPDX-License-Identifier: GPL-2.0-only
"""Finite interpretation of recovered LAN4 C, not compilation or silicon simulation.

MMIO, DMA and kernel calls use explicit single-owner mocks. Arithmetic casts and
bitwise complements here cover the used unsigned fields only, not full C typing.
The allocation/hash implementations, ABI, cache coherence, concurrency, elapsed
poll timing, startup state and register meanings are NOT verified by this model.
Unknown syntax/calls fail. No real hardware, network or toolchain is invoked.
"""
import ast
import re
from pycparser import c_ast as A, c_parser
from rx_transport_model import Interpreter, Ptr, Ref, Return, truth, DRIVERS
from idm_core import load_spec, expression


class Jump(Exception):
    pass


def sources():
    names = ('skd840n-idm-rx.h', 'skd840n-lan4-format.h', 'skd840n-lan4.h',
             'skd840n-lan4-hw.c', 'skd840n-lan4.c')
    text = '\n'.join((DRIVERS / n).read_text() for n in names)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
    text = re.sub(r'^#.*$', '', text, flags=re.M).replace('__iomem', '')
    text = text.replace('max_t(u32,', 'model_max(')
    text = re.sub(r'container_of\(([^;]+?),\s*struct lan4_priv,\s*(?:napi|tick)\)',
                  r'model_owner(\1)', text)
    text = text.replace('static DEVICE_ATTR_RO(test_state);',
                        'static struct device_attribute dev_attr_test_state;')
    text = text.replace('ATTRIBUTE_GROUPS(lan4);', 'void *lan4_groups;')
    text = re.sub(r'^(?:builtin_platform_driver|MODULE_\w+)\(.*?\);', '', text, flags=re.M)
    prefix = '''typedef unsigned char u8; typedef unsigned short u16;
    typedef unsigned int u32; typedef unsigned long long u64;
    typedef unsigned int __le32; typedef unsigned short __be16;
    typedef unsigned long long dma_addr_t; typedef unsigned long long resource_size_t;
    typedef int bool; typedef int irqreturn_t; typedef int netdev_tx_t;
    typedef long ssize_t; typedef unsigned long size_t;
    typedef int spinlock_t; typedef long atomic64_t;
    '''
    return c_parser.CParser().parse(prefix + text)


class C(Interpreter):
    def __init__(self, hooks):
        self.hooks = hooks
        self.steps = 0
        self.constants = load_spec()['constants']
        for name in ('skd840n-lan4-format.h', 'skd840n-lan4.h', 'skd840n-lan4.c'):
            for key, value in re.findall(r'^#define[ \t]+([A-Z0-9_]+)[ \t]+([^\n]+)$',
                                         (DRIVERS / name).read_text(), re.M):
                self.constants[key] = expression(value, self.constants)
        self.constants.update(NULL=None, true=1, false=0, EINVAL=22, ENODEV=19,
            ENODATA=61, ERANGE=34, ETIMEDOUT=110, EBUSY=16, ENOMEM=12,
            EDQUOT=122, EOVERFLOW=75, HZ=100, jiffies=100, ETH_ZLEN=60,
            CHECKSUM_PARTIAL=3, NETDEV_TX_OK=0, NETDEV_TX_BUSY=16,
            NAPI_STATE_SCHED=0, IRQ_NONE=0, IRQ_HANDLED=1,
            MII_BMCR=0, MII_BMSR=1, MII_PHYSID1=2, MII_PHYSID2=3,
            MII_CTRL1000=9, MII_STAT1000=10, LAN4_CLOCKS=8, LAN4_IRQS=4)
        self.tree = sources()
        self.functions = {x.decl.name: x for x in self.tree.ext if isinstance(x, A.FuncDef)}
        self.constants.update((name, name) for name in self.functions)

    def call(self, name, *args):
        if name in self.hooks:
            return self.hooks[name](*args)
        if name not in self.functions:
            raise ValueError('unmodeled function: ' + name)
        node = self.functions[name]
        params = node.decl.type.args.params or []
        if len(params) != len(args):
            raise ValueError('argument count: ' + name)
        env = dict(self.constants)
        env.update((p.name, v) for p, v in zip(params, args))
        items = node.body.block_items or []
        labels = {x.name: i for i, x in enumerate(items) if isinstance(x, A.Label)}
        pos = 0
        try:
            while pos < len(items):
                try:
                    self.stmt(items[pos], env)
                    pos += 1
                except Jump as j:
                    if j.args[0] not in labels:
                        raise ValueError('unsupported non-root label')
                    pos = labels[j.args[0]]
        except Return as r:
            return r.args[0]
        return None

    def ref(self, node, env):
        if isinstance(node, A.StructRef):
            value = self.expr(node.name, env)
            if isinstance(value, Ref):
                value = value.get()
            return Ref(value, node.field.name)
        if isinstance(node, A.ArrayRef):
            value = self.expr(node.name, env)
            index = self.expr(node.subscript, env)
            if isinstance(value, Ref):
                if index != 0:
                    raise IndexError('scalar pointer array index')
                return value
        return super().ref(node, env)

    def expr(self, node, env):
        if isinstance(node, A.Constant) and node.type == 'string':
            return ast.literal_eval(node.value)
        if isinstance(node, A.TernaryOp):
            return self.expr(node.iftrue if truth(self.expr(node.cond, env)) else node.iffalse, env)
        if isinstance(node, A.UnaryOp) and node.op == '~':
            return ~self.expr(node.expr, env) & 0xffffffff
        if isinstance(node, A.UnaryOp) and node.op in ('p--', '--'):
            ref = self.ref(node.expr, env)
            v = ref.get(); ref.set(v - 1)
            return v if node.op == 'p--' else v - 1
        if isinstance(node, A.Assignment) and node.op in ('&=', '|='):
            r = self.ref(node.lvalue, env); v = self.expr(node.rvalue, env)
            r.set(r.get() & v if node.op == '&=' else r.get() | v)
            return r.get()
        if isinstance(node, A.FuncCall) and isinstance(node.name, A.ID):
            name = node.name.name
            args = node.args.exprs if node.args else []
            if name == 'readl_poll_timeout':
                addr = self.expr(args[0], env)
                self.ref(args[1], env).set(self.call('readl', addr))
                return 0 if truth(self.expr(args[2], env)) else -110
            if name in ('spin_lock_irqsave', 'spin_unlock_irqrestore'):
                self.call('trace', name)
                return 0
        if isinstance(node, A.Cast):
            value = self.expr(node.expr, env)
            typ = node.to_type.type
            if isinstance(typ, A.TypeDecl) and isinstance(typ.type, A.IdentifierType):
                name = typ.type.names[0]
                if name in ('u8', 'u16', 'u32', 'u64'):
                    return value & ((1 << int(name[1:])) - 1)
            return value
        return super().expr(node, env)

    def stmt(self, node, env):
        if isinstance(node, A.Goto):
            raise Jump(node.name)
        if isinstance(node, A.Label):
            return self.stmt(node.stmt, env)
        if isinstance(node, A.EmptyStatement):
            return
        if isinstance(node, A.Decl):
            typ = node.type
            if isinstance(typ, A.ArrayDecl):
                if isinstance(typ.type.type, A.Struct):
                    fields = [d.name for d in typ.type.type.decls]
                    vals = [dict(zip(fields, [self.expr(v, env) for v in row.exprs]))
                            for row in node.init.exprs]
                elif node.init:
                    vals = [self.expr(v, env) for v in node.init.exprs]
                else:
                    vals = [0] * self.expr(typ.dim, env)
                env[node.name] = Ptr(vals, label=node.name)
                return
            if isinstance(typ, A.TypeDecl) and isinstance(typ.type, A.Struct):
                if typ.type.name == 'skd840n_idm_rx_stats':
                    env[node.name] = dict(consumed=0, delivered=0, dropped=0,
                                         release_busy=0, not_ready=0, faults=0)
                    return
        return super().stmt(node, env)


def unpack(value):
    return value.get() if isinstance(value, Ref) else value


class Kernel:
    def __init__(self):
        self.trace = []
        self.mmio = {0x18080014: 1, 0x19000080: 0x1fd,
                     0x19024004: 0x1f, 0x19020018: 1, 0x190342a0: 1}
        hw = dict(dev='device', np=0x19000000, pps=0x18000000,
            bmu=Ptr([0] * 65536, label='bmu-bppe'), bmu_dma=0x94000000,
            tx_dma=0x96000000, free_dma=0x96500000, stage='new',
            wait_offset=0, wait_value=0,
            rx=dict(descriptors=0x96600000, normal_bp=0x96700000,
                    jumbo_bp=0x96710000, depth=1024))
        ndev = dict(carrier=True, queue=True)
        p = dict(hw=hw, ndev=ndev, error=0, running=True, retained=False,
            attempted=False, requested=0, napi_on=False, clocks_on=False,
            napi=dict(state=0), mdio_lock=0, tx_lock=0, irq_lock=0, control=0,
            tick=0, rx='rx', clocks=Ptr([dict(clk='clk')] * 8), mdio_reset=0,
            irqs=Ptr([33, 34, 35, 36]), producer=0, consumer=0, pending=0, done=0,
            submitted=0, completed=0, tx_progress=100, link_due=0,
            tx_data=Ptr(bytearray(1024 * 2304), label='txpayload'),
            tx_data_dma=0x96100000, tx_desc=Ptr([0] * (4 * 1024 * 8), label='txdesc'),
            lengths=Ptr([0] * 1024), rx_retries=0,
            **{n: 0 for n in ('rx_packets', 'rx_bytes', 'rx_dropped', 'rx_consumed',
                 'tx_packets', 'tx_bytes', 'tx_dropped', 'irq_count', 'rx_transport_drop',
                 'rx_busy', 'rx_not_ready')})
        ndev['priv'] = p
        self.p, self.hw, self.ndev = p, hw, ndev
        self.rx_work, self.rx_more, self.rx_count = 0, False, 0
        hooks = dict(readl=self.readl, writel=self.writel, trace=self.event,
            READ_ONCE=lambda x: x, write_once=lambda r, v: self.event('desc' if isinstance(r.mapping, list) else 'state', r.key, v),
            cpu_to_le32=lambda x: x & 0xffffffff,
            cpu_to_be16=lambda x: int.from_bytes(x.to_bytes(2, 'little'), 'big'),
            lower_32_bits=lambda x: x & 0xffffffff,
            BIT=lambda b: 1 << b, ARRAY_SIZE=lambda p: len(p.memory), model_max=max,
            unlikely=lambda x: x, model_owner=lambda x: self.p, to_delayed_work=lambda x: x,
            netdev_priv=lambda n: n['priv'], skb_is_gso=lambda s: s.get('gso', 0),
            skb_checksum_help=lambda s: s.get('csum_error', 0), skb_copy_bits=self.copy_skb,
            memset=self.memset, dma_wmb=lambda: self.event('dma_wmb'),
            dma_rmb=lambda: self.event('dma_rmb'),
            dev_kfree_skb_any=lambda s: self.event('free_skb'),
            netif_carrier_ok=lambda n: n['carrier'], netif_carrier_on=lambda n: n.update(carrier=True),
            netif_carrier_off=lambda n: n.update(carrier=False),
            netif_stop_queue=lambda n: n.update(queue=False), netif_wake_queue=lambda n: n.update(queue=True),
            netif_start_queue=lambda n: n.update(queue=True),
            netdev_sent_queue=lambda n, count: self.event('sent', count),
            netdev_completed_queue=lambda n, count, size: self.event('completed', count, size),
            netdev_reset_queue=lambda n: self.event('bql_reset'),
            time_after=lambda a, b: a > b, time_after_eq=lambda a, b: a >= b,
            msecs_to_jiffies=lambda x: max(1, x // 10),
            atomic64_inc=lambda r: r.set(r.get() + 1),
            atomic64_add=lambda v, r: r.set(r.get() + v), atomic64_set=lambda r, v: r.set(v),
            atomic64_read=lambda r: r.get(), cmpxchg=self.cmpxchg,
            test_bit=lambda b, r: (r.get() >> b) & 1,
            napi_schedule_prep=self.napi_prep,
            __napi_schedule=lambda n: self.event('schedule'),
            napi_complete_done=self.napi_complete,
            skd840n_idm_rx_poll=self.rx_poll, skd840n_idm_rx_get_stats=self.rx_stats,
            request_irq=lambda *a: self.event('request_irq', a[0]) or 0,
            clk_bulk_prepare_enable=lambda *a: self.event('clocks_on') or 0,
            clk_get_rate=lambda c: 2500000, reset_control_reset=lambda r: 0,
            lan4_phy=lambda p, status: 1 if status else 0,
            lan4_alloc=lambda p: self.event('alloc') or 0,
            skd840n_idm_rx_publish=lambda r: self.event('publish') or 0,
            lan4_free_unexposed=lambda p: self.event('free_unexposed'),
            clk_bulk_disable_unprepare=lambda *a: self.event('clocks_off'),
            IS_ERR=lambda x: False)
        for name in ('spin_lock_bh', 'spin_unlock_bh', 'spin_lock', 'spin_unlock',
                     'mutex_lock', 'mutex_unlock', 'usleep_range', 'dev_err', 'netdev_err',
                     'netdev_info', 'schedule_delayed_work', 'cancel_delayed_work_sync',
                     'synchronize_irq', 'netif_tx_disable'):
            hooks[name] = lambda *a, name=name: self.event(name)
        hooks['napi_enable'] = lambda n: self.event('napi_enable')
        hooks['napi_disable'] = lambda n: self.event('napi_disable')
        self.model = C(hooks)

    def event(self, *args):
        self.trace.append(args)

    def readl(self, address):
        self.trace.append(('read', address))
        return self.mmio.get(address, 0)

    def writel(self, value, address):
        if not 0x19000000 <= address < 0x1a000000 or address % 4:
            raise ValueError('unexpected/out-of-range MMIO write')
        self.trace.append(('write', address, value & 0xffffffff))
        self.mmio[address] = value & 0xffffffff
        if address == 0x19010004:
            self.mmio[address] = 0  # Explicit mock, not a silicon observation.

    def memset(self, p, value, size):
        if size < 0 or p.index < 0 or p.index + size > len(p.memory):
            raise IndexError('memset boundary')
        self.trace.append(('memset', p.label, p.index, size))
        for i in range(size):
            p.memory[p.index + i] = value
        return p

    def copy_skb(self, skb, offset, target, size):
        if skb.get('copy_error'):
            return -14
        if size != skb['len'] or offset != 0:
            raise ValueError('unsupported skb copy range')
        if target.index + size > len(target.memory):
            raise IndexError('skb copy boundary')
        target.memory[target.index:target.index + size] = skb['data']
        self.trace.append(('copy', target.index, size))
        return 0

    def cmpxchg(self, ref, old, new):
        value = ref.get()
        if value == old:
            ref.set(new)
        return value

    def napi_prep(self, n):
        n = unpack(n)
        self.trace.append(('prep',))
        if n['state'] & 1:
            return False
        n['state'] |= 1
        return True

    def napi_complete(self, n, work):
        n = unpack(n)
        self.trace.append(('complete', work))
        n['state'] &= ~1
        return True

    def rx_poll(self, rx, budget, more):
        more.set(self.rx_more)
        self.event('rx_poll', budget)
        return self.rx_work

    def rx_stats(self, rx, ref):
        ref.get().update(consumed=self.rx_count)

    def call(self, name, *args):
        self.model.steps = 0
        return self.model.call(name, *args)

    def skb(self, size=98, **extra):
        return dict(len=size, data=bytes([0xa5] * size), ip_summed=0, **extra)
