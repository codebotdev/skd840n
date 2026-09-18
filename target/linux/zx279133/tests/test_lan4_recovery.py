#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline recovery checks. Does not invoke make, compiler, hardware or network."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests')]
from lan4_model import Kernel, Ptr, Ref


def load_selector():
    spec = importlib.util.spec_from_file_location('selector', ROOT / 'tools/select-lan4.py')
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.k = Kernel()

    def test_direct_descriptor_words(self):
        w = Ptr([0xdeadbeef] * 8)
        self.assertEqual(self.k.call('skd840n_direct_tx_words', 0x96100000, 98, w), 0)
        self.assertEqual(w.memory, [0x96100000, 0x0f8000c4, 0x00430000, 0, 0, 0, 0x1e000000, 0])

    def test_invalid_frame_keeps_output(self):
        for n in (0, 59, 1519, 0xffffffff):
            w = Ptr([123] * 8)
            self.assertEqual(self.k.call('skd840n_direct_tx_words', 0x96100000, n, w), -22)
            self.assertEqual(w.memory, [123] * 8)

    def test_dma32_end_boundary(self):
        for dma in (0, 1 << 32, (1 << 32) - 2303):
            w = Ptr([123] * 8)
            self.assertEqual(self.k.call('skd840n_direct_tx_words', dma, 60, w), -34)
            self.assertEqual(w.memory, [123] * 8)
        self.assertEqual(self.k.call('skd840n_direct_tx_words', (1 << 32) - 2304, 60, Ptr([0]*8)), 0)

    def test_completed_wrap_and_impossible_delta(self):
        n = [77]
        self.assertEqual(self.k.call('skd840n_direct_completed', 2, 65534, 4, Ref(n, 0)), 0)
        self.assertEqual(n[0], 4)
        self.assertEqual(self.k.call('skd840n_direct_completed', 3, 0, 2, Ref(n, 0)), -75)
        self.assertEqual(n[0], 4)

    def test_completed_invalid_width_or_full_ring(self):
        for now, before, pending in ((65536, 0, 0), (0, 65536, 0), (0, 0, 1024)):
            self.assertEqual(self.k.call('skd840n_direct_completed', now, before, pending, Ptr([0])), -22)

    def test_gigabit_requires_current_link_and_an_result(self):
        self.assertEqual(self.k.call('skd840n_direct_gigabit', 0x1140, 0x796d, 0x200, 0x800), 1)
        for bmcr, bmsr, adv, lp in ((0x1140,0x7969,0x200,0x800), (0x1140,4,0x200,0x800),
                                  (0x1140,0x796d,0x200,0x8800), (0x1140,0x796d,0,0x800)):
            self.assertEqual(self.k.call('skd840n_direct_gigabit', bmcr,bmsr,adv,lp), 0)

    def test_gigabit_rejects_all_ones_and_powerdown(self):
        for i in range(4):
            a = [0x1140, 0x796d, 0x200, 0x800]
            a[i] = 0xffff
            self.assertEqual(self.k.call('skd840n_direct_gigabit', *a), -61)
        self.assertEqual(self.k.call('skd840n_direct_gigabit', 0x1940,0x796d,0x200,0x800),0)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.k = Kernel()

    def test_tx_private_copy_padding_and_publication_order(self):
        k = self.k
        k.p['tx_data'].memory[:256] = b'\xcc' * 256
        self.assertEqual(k.call('lan4_xmit', k.skb(42), k.ndev), 0)
        self.assertEqual(k.p['tx_data'].memory[128:170], b'\xa5' * 42)
        self.assertEqual(k.p['tx_data'].memory[170:188], bytes(18))
        self.assertEqual(k.p['tx_data'].memory[:128], b'\xcc' * 128)
        events = [e[0] for e in k.trace]
        self.assertLess(events.index('copy'), events.index('desc'))
        self.assertLess(max(i for i,e in enumerate(events) if e=='desc'), events.index('dma_wmb'))
        bell = ('write', 0x192800a4, 1 << 17)
        self.assertIn(bell, k.trace)
        self.assertLess(events.index('dma_wmb'), k.trace.index(bell))
        self.assertEqual(k.p['pending'], 1)

    def test_tx_copy_error_has_no_doorbell(self):
        k=self.k
        k.call('lan4_xmit', k.skb(copy_error=True), k.ndev)
        self.assertFalse(any(e[0]=='write' and e[1]==0x192800a4 for e in k.trace))
        self.assertEqual((k.p['pending'],k.p['submitted'],k.p['tx_dropped']),(0,0,1))

    def test_tx_oversize_and_offline_drop(self):
        k=self.k
        k.call('lan4_xmit',k.skb(1519),k.ndev)
        k.p['running']=False
        k.call('lan4_xmit',k.skb(),k.ndev)
        self.assertEqual(k.p['tx_dropped'],2)
        self.assertFalse(any(e[0]=='desc' for e in k.trace))

    def test_limit_stops_before_tx_slot_reuse(self):
        k=self.k
        k.p['submitted']=512
        k.p['producer']=512
        k.call('lan4_xmit',k.skb(),k.ndev)
        self.assertEqual(k.p['error'],-122)
        self.assertFalse(k.p['running'])
        self.assertEqual(k.p['submitted'],512)
        self.assertFalse(any(e[0] in ('desc','free_unexposed') for e in k.trace))

    def test_completion_accounting_no_buffer_release(self):
        k=self.k
        for _ in range(3): k.call('lan4_xmit',k.skb(),k.ndev)
        k.mmio[0x192800b0]=3
        k.trace.clear()
        self.assertEqual(k.call('lan4_reclaim',k.p),0)
        self.assertEqual((k.p['pending'],k.p['completed'],k.p['tx_bytes']),(0,3,294))
        self.assertIn(('completed',3,294),k.trace)
        self.assertFalse(any('free' in e[0] for e in k.trace))

    def test_impossible_completion_does_not_advance(self):
        k=self.k
        k.mmio[0x192800b0]=4
        self.assertEqual(k.call('lan4_reclaim',k.p),-75)
        self.assertEqual(k.p['completed'],0)

    def test_budget_zero_is_tx_only(self):
        k=self.k
        self.assertEqual(k.call('lan4_poll',Ref(k.p,'napi'),0),0)
        self.assertFalse(any(e[0] in ('rx_poll','complete') for e in k.trace))

    def test_more_work_keeps_napi_scheduled(self):
        k=self.k
        k.rx_more=True; k.rx_work=2; k.rx_count=2
        self.assertEqual(k.call('lan4_poll',Ref(k.p,'napi'),64),64)
        self.assertFalse(any(e[0]=='complete' for e in k.trace))

    def test_busy_zero_waits_for_timer_with_irqs_masked(self):
        k=self.k
        k.rx_more=True; k.rx_work=0
        k.mmio[0x19280040]=0x07ffffff
        self.assertEqual(k.call('lan4_poll',Ref(k.p,'napi'),64),0)
        self.assertIn(('complete',0),k.trace)
        self.assertEqual(k.mmio[0x19280040],0x07ffffff)
        self.assertEqual(k.p['rx_retries'],1)

    def test_irq_masks_before_napi_schedule(self):
        k=self.k
        k.call('lan4_schedule',k.p)
        self.assertLess(k.trace.index(('write',0x19280040,0x07ffffff)),k.trace.index(('schedule',)))
        self.assertEqual(k.p['napi']['state'],1)
        k.call('lan4_mask',k.p,False)
        self.assertEqual(k.mmio[0x19280040],0x07ffffff)

    def test_current_phy_state_controls_carrier(self):
        k=self.k
        k.ndev['carrier']=False
        k.call('lan4_tick',0)
        self.assertTrue(k.ndev['carrier'])
        k.model.hooks['lan4_phy']=lambda *a: 0
        k.p['link_due']=0
        k.call('lan4_tick',0)
        self.assertFalse(k.ndev['carrier'])

    def test_stop_retains_exposed_resources(self):
        k=self.k
        k.p.update(retained=True,attempted=True,requested=4,napi_on=True)
        k.call('lan4_stop',k.ndev)
        self.assertFalse(k.p['running'])
        self.assertTrue(k.p['retained'])
        self.assertEqual(sum(e[0]=='synchronize_irq' for e in k.trace),4)
        self.assertFalse(any(e[0] in ('free_unexposed','clocks_off') for e in k.trace))
        self.assertEqual(k.call('lan4_quiesce',k.p),-16)

    def test_open_once_and_publish_after_irq_napi(self):
        k=self.k
        self.assertEqual(k.call('lan4_open',k.ndev),0)
        self.assertTrue(k.p['retained'])
        event=[e[0] for e in k.trace]
        self.assertLess(event.index('alloc'),event.index('request_irq'))
        self.assertLess(event.index('napi_enable'),event.index('publish'))
        self.assertEqual(k.call('lan4_open',k.ndev),-16)

    def test_phy_mismatch_rolls_back_before_hardware(self):
        k=self.k
        k.model.hooks['lan4_phy']=lambda *a: -19
        self.assertEqual(k.call('lan4_open',k.ndev),-19)
        self.assertIn(('free_unexposed',),k.trace)
        self.assertIn(('clocks_off',),k.trace)
        self.assertFalse(any(e[0]=='write' for e in k.trace))

    def test_prepare_failure_quarantines_without_free(self):
        k=self.k
        k.mmio[0x18080014]=0
        self.assertEqual(k.call('lan4_open',k.ndev),-110)
        self.assertTrue(k.p['retained'])
        self.assertFalse(k.p['running'])
        self.assertFalse(any(e[0] in ('free_unexposed','clocks_off','publish') for e in k.trace))

    def test_all_ones_ready_is_rejected(self):
        k=self.k
        k.mmio[0x19000080]=0xffffffff
        self.assertEqual(k.call('skd840n_lan4_prepare',k.hw),-110)
        self.assertEqual(k.hw['wait_offset'],0x80)
        self.assertFalse(any(e[0]=='write' and e[1]==0x19280004 for e in k.trace))

    def test_publish_failure_stops_software(self):
        k=self.k
        k.model.hooks['skd840n_idm_rx_publish']=lambda *a: -5
        self.assertEqual(k.call('lan4_open',k.ndev),-5)
        self.assertTrue(k.p['retained'])
        self.assertIn(('napi_disable',),k.trace)
        self.assertFalse(k.p['running'])


class SelectorTests(unittest.TestCase):
    TEXT='CONFIG_TARGET_zx279133=y\nCONFIG_TARGET_zx279133_generic=y\nCONFIG_PACKAGE_busybox=y\nCONFIG_TARGET_ALL_PROFILES=y\nCONFIG_TARGET_zx279133_generic_DEVICE_skyworth_sk-d840n-mdio=y\n'

    def test_preserves_packages_selects_one_profile(self):
        s=load_selector(); out=s.select(self.TEXT)
        self.assertIn('CONFIG_PACKAGE_busybox=y',out)
        self.assertIn(s.SELECTED+'=y',out)
        self.assertIn('# CONFIG_TARGET_ALL_PROFILES is not set',out)
        self.assertNotIn('DEVICE_skyworth_sk-d840n-mdio=y',out)
        self.assertEqual(s.select(out),out)

    def test_wrong_target_rejected(self):
        with self.assertRaises(ValueError): load_selector().select('CONFIG_TARGET_x86=y\n')

    def test_cli_backup_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'.config'; p.write_text(self.TEXT); p.chmod(0o600)
            command=[sys.executable,str(ROOT/'tools/select-lan4.py'),str(p)]
            r=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            backups=list(Path(d).glob('.config.before-*'))
            self.assertEqual(len(backups),1)
            self.assertEqual(backups[0].read_text(),self.TEXT)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            p.unlink();p.symlink_to(backups[0])
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,1)


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.d=Path(self.temp.name); self.state=self.d/'state'; self.calls=self.d/'calls'
        self.state.write_text('version=lan4-direct-recovery-v1 interface=lan4test stage=registered-down running=0 error=0 link_error=0\nattempted=0 dma_retained=0 carrier=0 packet_limit=512\n')
        self.stats=self.d/'statistics'; self.stats.mkdir()
        for key in ('rx_packets','rx_bytes','rx_errors','rx_dropped','tx_packets','tx_bytes','tx_errors','tx_dropped'):
            (self.stats/key).write_text('0\n')
        self.script=self.d/'helper'
        self.script.write_text((ROOT/'base-files/usr/sbin/skd840n-lan4-test').read_text().replace('/sys/class/net/lan4test/device/test_state',str(self.state)).replace('/sys/class/net/lan4test/statistics',str(self.stats)))
        (self.d/'id').write_text('#!/bin/sh\necho "${FAKE_UID:-0}"\n')
        (self.d/'ip').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\ncase "$*" in "link set dev lan4test up") exit "${UP_RC:-0}" ;; esac\nexit 0\n')
        for n in ('id','ip'): (self.d/n).chmod(0o755)
        self.env={**os.environ,'PATH':str(self.d)+':/usr/bin:/bin','CALLS':str(self.calls)}

    def run_script(self,*args):
        return subprocess.run(['sh',str(self.script),*args],env=self.env,capture_output=True,text=True)

    def test_syntax_and_bad_arguments_no_ip(self):
        subprocess.run(['sh','-n',str(self.script)],check=True)
        for args in ((),('start','extra'),('bogus',)):
            self.assertEqual(self.run_script(*args).returncode,2)
        self.assertFalse(self.calls.exists())

    def test_start_uses_real_name_and_address(self):
        r=self.run_script('start');self.assertEqual(r.returncode,0,r.stderr)
        calls=self.calls.read_text()
        self.assertIn('link set dev lan4test up',calls)
        self.assertIn('addr add 192.168.1.1/24 dev lan4test',calls)
        self.assertNotIn('dev lan4 ',calls)

    def test_repeat_or_wrong_version_rejected(self):
        original=self.state.read_text()
        for value in (original.replace('attempted=0','attempted=1'),original.replace('recovery-v1','unknown')):
            self.state.write_text(value)
            self.assertEqual(self.run_script('start').returncode,1)
        self.assertFalse(self.calls.exists())

    def test_nonroot_and_failed_up_no_address(self):
        self.env['FAKE_UID']='1000'
        self.assertEqual(self.run_script('start').returncode,1)
        self.assertFalse(self.calls.exists())
        self.env['FAKE_UID']='0';self.env['UP_RC']='1'
        self.assertEqual(self.run_script('start').returncode,1)
        self.assertNotIn('addr add',self.calls.read_text())

    def test_status_reports_fault_and_stop_is_explicit(self):
        self.state.write_text(self.state.read_text().replace(' error=0',' error=-110'))
        self.assertEqual(self.run_script('status').returncode,1)
        self.assertNotIn('link set',self.calls.read_text())
        r=self.run_script('stop')
        self.assertEqual(r.returncode,1) # underlying recorded fault is retained
        self.assertIn('link set dev lan4test down',self.calls.read_text())


class SourceTests(unittest.TestCase):
    def test_build_wiring_and_probe_boundary(self):
        d=ROOT/'files-6.12/drivers/net/ethernet/zte'
        src=(d/'skd840n-lan4.c').read_text()
        probe=src[src.index('static int lan4_probe'):src.index('static void lan4_shutdown')]
        self.assertLess(probe.index('sysfs_create_groups('),probe.index('register_netdev('))
        self.assertNotIn('dma_alloc_coherent(',probe)
        self.assertNotIn('writel(',probe)
        self.assertIn('CONFIG_SKD840N_LAN4_TEST=y', (ROOT/'config-6.12').read_text())
        self.assertIn('skd840n-lan4-hw.o',(d/'Makefile').read_text())
        self.assertNotIn('request_firmware',src+(d/'skd840n-lan4-hw.c').read_text())

    def test_only_dedicated_dts_activates_experiment(self):
        dts=(ROOT/'dts/zx279133-skyworth-sk-d840n-lan4.dts').read_text()
        self.assertIn('0x94000000',dts)
        self.assertIn('0x04000000',dts)
        self.assertIn('skyworth,sk-d840n-lan4-direct',dts)
        self.assertIn('&mdio0_diag',dts)
        img=(ROOT/'image/Makefile').read_text()
        for profile in ('skyworth_sk-d840n','skyworth_sk-d840n-mdio','skyworth_sk-d840n-io','skyworth_sk-d840n-lan4'):
            self.assertIn('define Device/'+profile+'\n',img)
        self.assertIn('KERNEL_LOADADDR := 0x80000000',img)
        self.assertIn('DEVICE_DTS_CONFIG := conf@133',img)


if __name__=='__main__':
    unittest.main(verbosity=2)
