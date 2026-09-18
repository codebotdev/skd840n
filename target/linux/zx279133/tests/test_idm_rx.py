#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Interpreted RX control-flow and source checks; never compiles or runs DMA."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / 'files-6.12/drivers/net/ethernet/zte'
SOURCE = (DRIVERS / 'skd840n-idm-rx.c').read_text()
try:
    from rx_transport_model import Hardware, Ptr, function
except ImportError:
    Hardware = None


@unittest.skipIf(Hardware is None, 'pycparser required for actual-C flow tests')
class FlowTests(unittest.TestCase):
    def setUp(self):
        self.h = Hardware()

    def writes(self, reg):
        return [x[2] for x in self.h.trace if x[:2] == ('write', reg)]

    def test_all_24_queues_including_trap6_and_last23(self):
        for q in range(24):
            self.h.frame(q)
        self.assertEqual(self.h.poll(64), (24, False))
        self.assertEqual([x[0] for x in self.h.received], list(range(24)))
        self.assertEqual(self.writes(0x88), [1 | q << 12 for q in range(24)])
        self.assertEqual(self.h.counts, [0] * 24)

    def test_payload_starts_at_128_not_at_descriptor_address(self):
        self.h.frame(6)
        self.h.poll(64)
        self.assertEqual(self.h.received[0][1], bytes([0xab] * 98))
        self.assertEqual(self.h.received[0][2][1], 98 | 3 << 16)

    def test_dma_sync_and_release_before_big_endian_bp_refill(self):
        b = self.h.frame(6, dma=0x87654320)
        self.h.poll(64)
        t = self.h.trace
        events = (
            ('rmb',), ('sync_cpu', b['dma']), ('deliver', 6),
            ('sync_device', b['dma']), ('memory_write', (6 * 1024) * 8, 0),
            ('write', 0x88, 0x6001), ('memory_write', 4096, 0x20436587),
            ('write', 0x100, 1))
        for event in events:
            self.assertIn(event, t)
        positions = [t.index(event) for event in events]
        self.assertEqual(positions, sorted(positions))
        for reg in (0x88, 0x100):
            position = next(i for i, e in enumerate(t) if e[:2] == ('write', reg))
            self.assertEqual(t[position - 1], ('wmb',))
        self.assertEqual(self.h.rx['normal_bp'].memory[4096], 0x20436587)

    def test_busy_release_retains_buffer_without_redelivery_or_refill(self):
        b = self.h.frame(6)
        self.h.busy = True
        self.assertEqual(self.h.poll(64), (1, True))
        self.assertFalse(b['posted'])
        self.assertIs(self.h.rx['pending'].memory[6], b)
        self.assertEqual(self.h.poll(64), (0, True))
        self.assertEqual(len(self.h.received), 1)
        self.assertFalse(self.writes(0x88))
        self.assertFalse(self.writes(0x100))
        self.h.busy = False
        self.assertEqual(self.h.poll(64), (0, False))
        self.assertEqual(len(self.h.received), 1)
        self.assertEqual(self.writes(0x88), [0x6001])
        self.assertEqual(self.writes(0x100), [1])
        self.assertTrue(b['posted'])

    def test_empty_descriptor_with_pending_count_is_retry_not_consumed(self):
        self.h.counts[23] = 1
        self.assertEqual(self.h.poll(64), (0, True))
        self.assertEqual(self.h.rx['consumer'].memory[23], 0)
        self.assertFalse(self.writes(0x88))

    def test_unknown_dma_fault_does_not_dereference_or_release(self):
        self.h.frame(6, dma=0x90000000)
        self.h.by_dma.clear()
        self.assertEqual(self.h.poll(64)[0], -117)
        self.assertEqual(self.h.mask & 0xffffff, 0xffffff)
        self.assertFalse(self.h.received)
        self.assertFalse(self.writes(0x88))
        self.assertFalse(self.writes(0x100))
        self.assertNotIn(('free',), self.h.trace)
        self.assertEqual(self.h.rx['descriptors'].memory[(6 * 1024) * 8], 0x90000000)

    def test_duplicate_held_address_in_another_queue_is_quarantined(self):
        b = self.h.frame(0, dma=0x90000000)
        self.h.frame(1, dma=0x90000000)
        self.h.by_dma[b['dma']] = b
        self.h.busy = True
        self.assertEqual(self.h.poll(64)[0], -117)
        self.assertEqual(len(self.h.received), 1)
        self.assertFalse(self.writes(0x100))
        self.assertFalse(b['posted'])

    def test_pool_mismatch_fault_does_not_recycle_into_wrong_pool(self):
        self.h.frame(0, flags=98 | 0x4000)
        self.assertEqual(self.h.poll(64)[0], -117)
        self.assertFalse(self.writes(0x100))

    def test_short_oversize_omci_reordered_frames_are_dropped(self):
        for q, flags in enumerate((0, 13, 2049, 0x3fff, 98 | 0x8000, 98 | 0x20000000)):
            self.h.frame(q, flags=flags)
        self.assertEqual(self.h.poll(64), (6, False))
        self.assertFalse(self.h.received)
        self.assertEqual(self.h.rx['stats']['dropped'], 6)
        self.assertEqual(self.writes(0x100), [1] * 6)
        self.assertFalse(any(e[0] == 'sync_cpu' for e in self.h.trace))

    def test_known_jumbo_is_recycled_without_interpreting_payload(self):
        b = self.h.frame(23, jumbo=True)
        self.assertEqual(self.h.poll(64), (1, False))
        self.assertFalse(self.h.received)
        self.assertEqual(self.writes(0x100), [0x10000])
        self.assertTrue(b['posted'])

    def test_receiver_allocation_failure_recycles_once(self):
        self.h.frame(6)
        self.h.receive_error = -12
        self.assertEqual(self.h.poll(64), (1, False))
        self.assertEqual(self.h.rx['stats']['delivered'], 0)
        self.assertEqual(self.h.rx['stats']['dropped'], 1)
        self.assertEqual(self.writes(0x100), [1])

    def test_budget_one_rotates_so_queue0_cannot_starve_queue6(self):
        self.h.frame(0)
        self.h.frame(6)
        self.assertEqual(self.h.poll(1), (1, True))
        self.h.frame(0, slot=1)
        self.assertEqual(self.h.poll(1), (1, True))
        self.assertEqual([x[0] for x in self.h.received], [0, 6])

    def test_quantum_reports_more_even_when_global_budget_not_exhausted(self):
        for slot in range(40):
            self.h.frame(6, slot=slot)
        self.assertEqual(self.h.poll(64), (32, True))
        self.assertEqual(self.h.poll(64), (8, False))
        self.assertEqual(self.h.rx['stats']['consumed'], 40)

    def test_zero_and_negative_budget_do_not_touch_rx(self):
        self.h.frame(6)
        self.assertEqual(self.h.poll(0), (0, False))
        self.assertEqual(self.h.poll(-1)[0], -22)
        self.assertFalse(self.h.trace)

    def test_impossible_pending_count_faults_without_clamping(self):
        self.h.counts[23] = 1025
        self.assertEqual(self.h.poll(64)[0], -75)
        self.assertFalse(self.h.received)
        self.assertFalse(self.writes(0x88))

    def test_descriptor_and_bp_wrap(self):
        self.h.rx['consumer'].memory[6] = 1023
        self.h.rx['normal_producer'] = 8191
        self.h.frame(6, slot=1023)
        self.assertEqual(self.h.poll(64), (1, False))
        self.assertEqual(self.h.rx['consumer'].memory[6], 0)
        self.assertEqual(self.h.rx['normal_producer'], 0)
        self.h.frame(6, slot=0)
        self.assertEqual(self.h.poll(64), (1, False))
        self.assertEqual(self.h.rx['consumer'].memory[6], 1)

    def test_failed_halt_retains_handle_and_cannot_republish(self):
        h = self.h
        h.stop_error = -110
        handle = [h.rx]
        self.assertEqual(h.model.call('skd840n_idm_rx_destroy', Ptr(handle)), -110)
        self.assertIs(handle[0], h.rx)
        self.assertNotIn(('free',), h.trace)
        self.assertFalse(h.rx['running'])
        self.assertTrue(h.rx['stopping'])
        self.assertEqual(h.model.call('skd840n_idm_rx_publish', h.rx), -22)
        h.stop_error = 0
        self.assertEqual(h.model.call('skd840n_idm_rx_destroy', Ptr(handle)), 0)
        self.assertIsNone(handle[0])
        self.assertLess(h.trace.index(('quiesce', 0)), h.trace.index(('free',)))

    def test_unexposed_rollback_does_not_touch_hardware(self):
        self.h.rx['exposed'] = False
        handle = [self.h.rx]
        self.assertEqual(self.h.model.call('skd840n_idm_rx_destroy', Ptr(handle)), 0)
        self.assertEqual(self.h.trace, [('free',)])
        self.assertIsNone(handle[0])

    def test_initial_credits_are_bounded_and_cannot_be_published_twice(self):
        self.h.rx['running'] = False
        self.h.rx['buffers'] = Ptr([{'posted': False} for _ in range(4224)])
        self.assertEqual(self.h.model.call('skd840n_idm_rx_publish', self.h.rx), 0)
        self.assertEqual(self.writes(0x100), [1024] * 4 + [128 << 16])
        self.assertTrue(all(x['posted'] for x in self.h.rx['buffers'].memory))
        self.assertEqual(self.h.model.call('skd840n_idm_rx_publish', self.h.rx), -22)
        self.assertEqual(len(self.writes(0x100)), 5)

    def test_register_arithmetic_and_endpoints(self):
        for q in range(24):
            output = [123]
            self.assertEqual(self.h.model.call('skd840n_idm_count_reg', q, Ptr(output)), 0)
            self.assertEqual(output[0], 0xc4 + 4 * (q // 2))
            self.assertEqual(self.h.model.call('skd840n_idm_count_value', 0x04000006, q, Ptr(output)), 0)
            self.assertEqual(output[0], 1024 if q & 1 else 6)
        output = [123]
        self.assertEqual(self.h.model.call('skd840n_idm_count_reg', 24, Ptr(output)), -22)
        self.assertEqual(output, [123])
        self.assertEqual(self.h.model.call('skd840n_idm_release_word', 23, 1024, Ptr(output)), 0)
        self.assertEqual(output, [0x17400])
        for q, n in ((24, 1), (0, 0), (0, 1025)):
            self.assertEqual(self.h.model.call('skd840n_idm_release_word', q, n, Ptr(output)), -22)
        self.assertEqual(self.h.model.call('skd840n_idm_refill_word', 1024, 1024, Ptr(output)), 0)
        self.assertEqual(output, [0x04000400])
        for a, b in ((0, 0), (1025, 0), (0, 1025)):
            self.assertEqual(self.h.model.call('skd840n_idm_refill_word', a, b, Ptr(output)), -22)

    def test_dma32_last_byte_and_frame_bounds_preserve_outputs(self):
        call = self.h.model.call
        self.assertEqual(call('skd840n_idm_buffer_range', 0xfffff700, 2304), 0)
        for base, length in ((0, 2304), (1 << 32, 1), (0xfffff701, 2304), (1, 0)):
            self.assertEqual(call('skd840n_idm_buffer_range', base, length), -34)
        for size in (14, 98, 1500, 2048):
            output = [123]
            self.assertEqual(call('skd840n_idm_frame_range', size, 2304, Ptr(output)), 0)
            self.assertEqual(output, [size])
        for size, capacity in ((14, 127), (98, 225), (13, 2304), (2049, 2304)):
            output = [123]
            self.assertEqual(call('skd840n_idm_frame_range', size, capacity, Ptr(output)), -90)
            self.assertEqual(output, [123])


class SourceTests(unittest.TestCase):
    def test_no_automatic_activation_or_fake_netdev(self):
        self.assertNotRegex(SOURCE, r'\b(?:module_init|module_platform_driver|late_initcall|register_netdev|netif_carrier_on)\s*\(')
        self.assertNotRegex(SOURCE, r'phys_to_virt|virt_to_phys|__va\s*\(|/dev/mem|request_firmware')

    def test_dma_mapping_failures_and_cleanup_are_explicit(self):
        self.assertIn('dma_mapping_error(dev, b->dma)', SOURCE)
        self.assertIn('b->mapped = true;', SOURCE)
        self.assertIn('if (b->mapped)', SOURCE)
        self.assertIn('dma_unmap_single', SOURCE)
        self.assertIn('dma_free_coherent', SOURCE)
        self.assertNotIn('devm_', SOURCE)
        self.assertIn('if (skd840n_rx_lookup(rx, key))', SOURCE)

    def test_endian_domains_and_sized_copy_contract(self):
        self.assertIn('__le32 *descriptors;', SOURCE)
        self.assertIn('__be32 *normal_bp;', SOURCE)
        self.assertIn('__be32 *jumbo_bp;', SOURCE)
        self.assertIn('skd840n_idm_frame_range(words[1], b->bytes, &length)', SOURCE)
        contract = (DRIVERS / 'skd840n-idm-rx.h').read_text()
        self.assertIn('must COPY data', contract)
        self.assertIn('NOT directly a NAPI poll return value', contract)
        self.assertIn('must retain the device', contract)

    def test_compile_wiring_has_explicit_target_setting(self):
        patch = (ROOT / 'patches-6.12/150-net-add-skd840n-idm-rx.patch').read_text()
        self.assertIn('CONFIG_SKD840N_IDM_RX', patch)
        self.assertIn('source "drivers/net/ethernet/zte/Kconfig"', patch)
        self.assertIn('CONFIG_SKD840N_IDM_RX=y', (ROOT / 'config-6.12').read_text())
        self.assertEqual((DRIVERS / 'Makefile').read_text().count('skd840n-idm-rx.o'), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
