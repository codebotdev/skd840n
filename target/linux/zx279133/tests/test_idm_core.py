#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""No compiler, preprocessor, firmware, device or network access."""
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from idm_core import HEADER, decode, layout, load_spec

SPEC = load_spec()
try:
    from idm_c_model import Model
except ImportError:
    Model = None

# Independent literal oracle transcribed from the stock AArch64 dump functions.
RX = ((0, 0, 32), (1, 0, 14), (1, 14, 1), (1, 15, 1), (1, 16, 6),
      (1, 22, 1), (1, 23, 6), (1, 29, 1), (2, 0, 16))
TX = ((0, 0, 32), (1, 1, 14), (1, 0, 1), (1, 16, 8), (1, 15, 1),
      (1, 24, 6), (1, 30, 1), (1, 31, 1), (2, 0, 1), (2, 1, 7),
      (2, 9, 7), (2, 22, 1), (2, 16, 6), (6, 0, 16), (6, 16, 9))
SIZES = [0xc0000, 0x20000, 0x8000, 0x4000, 0x1000, 0xc0000, 0x900000, 0x1f8000, 0x240000]
GROUPS = [0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 0, 0, 0]
MASKS = [0x071ffbfb, 0x00200000, 0x00400000, 0x00800404]


@unittest.skipIf(Model is None, 'pycparser is required for the interpreted C tests')
class CTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = Model(HEADER.read_text(), SPEC['constants'], SPEC['sizes'])

    def run_c(self, name, *args):
        return self.model.call('skd840n_idm_' + name, *args)

    def test_contiguous_masks_and_full_width_unsigned_wrap(self):
        for shift in range(32):
            for width in range(1, 33 - shift):
                mask = ((1 << width) - 1) << shift
                self.assertEqual(self.run_c('field_valid', 0, mask, shift), 1)
        for word, mask, shift in ((8, 1, 0), (0, 0, 0), (0, 5, 0), (0, 6, 0), (0, 1, 32)):
            self.assertEqual(self.run_c('field_valid', word, mask, shift), 0)

    def test_rx_fields_match_independent_instruction_oracle(self):
        self.check_fields('rx', RX)

    def test_tx_fields_match_independent_instruction_oracle(self):
        self.check_fields('tx', TX)

    def check_fields(self, kind, oracle):
        rng = random.Random(840)
        for spec, (word, shift, width) in zip(SPEC['fields'][kind], oracle):
            mask = ((1 << width) - 1) << shift
            self.assertEqual((spec['word'], spec['mask'], spec['shift']), (word, mask, shift))
            for _ in range(20):
                words = [rng.getrandbits(32) for _ in range(8)]
                out = [0xdeadbeef]
                self.assertEqual(self.run_c('get_field', words, 8, word, mask, shift, out), 0)
                self.assertEqual(out[0], (words[word] >> shift) & ((1 << width) - 1))
                value = rng.getrandbits(width)
                before = words[:]
                self.assertEqual(self.run_c('set_field', words, 8, word, mask, shift, value), 0)
                before[word] = (before[word] & ~mask) | (value << shift)
                self.assertEqual(words, before)

    def test_invalid_field_calls_preserve_outputs(self):
        original = [0xaaaaaaaa] * 8
        for word, mask, shift, count, value, rc in ((1, 0x7ffe, 1, 8, 0x4000, -34),
                (8, 1, 0, 8, 0, -22), (1, 5, 0, 8, 0, -22), (1, 1, 32, 8, 0, -22),
                (1, 1, 0, 7, 0, -90)):
            words = original[:]
            self.assertEqual(self.run_c('set_field', words, count, word, mask, shift, value), rc)
            self.assertEqual(words, original)
        out = [123]
        self.assertEqual(self.run_c('get_field', None, 8, 1, 1, 0, out), -22)
        self.assertEqual(self.run_c('get_field', original, 8, 1, 1, 0, None), -22)
        self.assertEqual(self.run_c('get_field', original, 7, 1, 1, 0, out), -90)
        self.assertEqual(out, [123])

    def test_factory_layout_is_reproduced_not_hardcoded(self):
        self.assertEqual(SPEC['sizes'], SIZES)
        out, used = [0] * 9, [0]
        self.assertEqual(self.run_c('plan', 0x80b33000, 0xee5000, out, 9, used), 0)
        self.assertEqual(out, [0x80b33000, 0x80bf3000, 0x80c13000, 0x80c1b000, 0x80c1f000,
                               0x80c20000, 0x80ce0000, 0x815e0000, 0x817d8000])
        self.assertEqual(used, [0xee5000])
        shifted = [0] * 9
        self.assertEqual(self.run_c('plan', 0x90000000, 0xee5000, shifted, 9, used), 0)
        self.assertEqual([b - a for a, b in zip(out, shifted)], [0xf4cd000] * 9)

    def test_layout_boundary_and_failure_atomicity(self):
        total = sum(SIZES)
        out, used = [17] * 9, [23]
        self.assertEqual(self.run_c('plan', (1 << 32) - total, total, out, 9, used), 0)
        for dma, size, count, rc in ((0x1001, total, 9, -22), (0x1000, total - 1, 9, -28),
                (1 << 32, total, 9, -34), ((1 << 32) - total + 4096, total, 9, -34),
                ((1 << 64) - 4096, total, 9, -34), (4096, total, 8, -22)):
            out, used = [17] * 9, [23]
            self.assertEqual(self.run_c('plan', dma, size, out, count, used), rc)
            self.assertEqual((out, used), ([17] * 9, [23]))
        self.assertEqual(self.run_c('plan', 4096, total, None, 9, used), -22)

    def test_irq_groups_reproduce_factory_masks(self):
        out = [0] * 4
        self.assertEqual(self.run_c('irq_masks', GROUPS, 27, out), 0)
        self.assertEqual(out, MASKS)
        self.assertEqual(sum(x.bit_count() for x in out), 27)
        self.assertEqual(out[0] | out[1] | out[2] | out[3], (1 << 27) - 1)

    def test_invalid_group_does_not_partially_publish_masks(self):
        for pos in range(27):
            groups, out = GROUPS[:], [0xabcdef] * 4
            groups[pos] = 4
            self.assertEqual(self.run_c('irq_masks', groups, 27, out), -22)
            self.assertEqual(out, [0xabcdef] * 4)
        self.assertEqual(self.run_c('irq_masks', GROUPS, 26, [0] * 4), -22)
        self.assertEqual(self.run_c('irq_masks', None, 27, [0] * 4), -22)

    def test_all_24_rx_events_not_just_queue_zero(self):
        for bit in range(32):
            self.assertEqual(self.run_c('rx_events', 1 << bit), 1 << bit if bit < 24 else 0)

    def test_route_table_queue_is_nine_bits_not_a_ring_number(self):
        for port in (0, 3, 15, 63):
            for queue in (0, 6, 403, 419, 511):
                out = [0]
                self.assertEqual(self.run_c('route_word', 0xa5a5ffff, port, queue, out), 0)
                self.assertEqual(out, [(0xa5a5ffff & ~0x7fff) | port | (queue << 6)])
        for port, queue in ((64, 0), (0, 512)):
            out = [123]
            self.assertEqual(self.run_c('route_word', 0, port, queue, out), -34)
            self.assertEqual(out, [123])
        self.assertEqual(self.run_c('route_word', 0, 0, 0, None), -22)

    def test_completion_wrap_and_overrun(self):
        for now, before, owned, delta in ((2, 65534, 4, 4), (0, 0, 10, 0), (100, 3, 100, 97)):
            out = [19]
            self.assertEqual(self.run_c('completed16', now, before, owned, out), 0)
            self.assertEqual(out, [delta])
        for now, before, owned, rc in ((2, 65534, 3, -75), (0, 1, 1, -75),
                                       (65536, 0, 0, -22), (0, 65536, 0, -22), (0, 0, 65536, -22)):
            out = [19]
            self.assertEqual(self.run_c('completed16', now, before, owned, out), rc)
            self.assertEqual(out, [19])


class OfflineTests(unittest.TestCase):
    def test_unknown_bits_retained_and_no_fake_ownership(self):
        for kind in ('rx', 'tx'):
            words = [0xffffffff] * 8
            result = decode(kind, words, SPEC)
            self.assertEqual(result['words'], ['ffffffff'] * 8)
            self.assertNotIn('owner', result['fields'])
            self.assertNotIn('reason', result['fields'])
            self.assertNotIn('valid', result['fields'])
            self.assertEqual(result['unclassified_bits'][3], 'ffffffff')
        self.assertEqual(decode('tx', words, SPEC)['unclassified_bits'][2], 'ff800100')
        self.assertEqual(decode('tx', words, SPEC)['unclassified_bits'][6], 'fe000000')

    def test_decode_rejects_wrong_sizes_and_non_u32(self):
        for words in ([0] * 7, [0] * 9, [-1] * 8, [1 << 32] * 8, [True] * 8):
            with self.assertRaises(ValueError):
                decode('rx', words, SPEC)

    def test_layout_is_contiguous_and_bounded(self):
        report = layout(0x90000000, 0xee5000, SPEC)
        self.assertEqual(report['bytes_used'], 0xee5000)
        self.assertEqual(report['hardware_recv_desc'], 'cpu_tx_desc')
        for a, b in zip(report['regions'], report['regions'][1:]):
            self.assertEqual(int(a['dma'], 16) + a['bytes'], int(b['dma'], 16))
        for dma, size in ((0x90000001, 0xee5000), (0x90000000, 0), (0xfffff000, 0xee5000)):
            with self.assertRaises(ValueError):
                layout(dma, size, SPEC)

    def test_cli_accepts_only_explicit_numeric_words(self):
        command = [sys.executable, str(ROOT / 'tools/idm_core.py')]
        result = subprocess.run(command + ['rx', '90000000', '07c30062'] + ['0'] * 6,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        decoded = json.loads(result.stdout)
        self.assertEqual((decoded['fields']['packet_length'], decoded['fields']['source_port']), (98, 3))
        for args in (['rx'] + ['0'] * 7, ['rx'] + ['0'] * 7 + ['100000000'],
                     ['rx'] + ['0'] * 7 + ['/dev/mem'], ['raw', 'file']):
            result = subprocess.run(command + args, text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)

    def test_spec_rejects_new_grammar_instead_of_evaluating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'header.h'
            for value in ('(1 << 3)', 'call()', '__import__("os")'):
                path.write_text(HEADER.read_text().replace('8U\n', value + '\n', 1))
                with self.assertRaises((ValueError, SyntaxError)):
                    load_spec(path)

    def test_evidence_geometry_and_masks_match_implementation(self):
        data = json.loads((ROOT / 'docs/factory-ethernet-20260918.json').read_text())
        self.assertEqual([x['bytes'] for x in data['idm_geometry']['regions']], SIZES)
        self.assertEqual(data['idm_geometry']['total_bytes'], sum(SIZES))
        self.assertEqual(data['irq']['queue_to_group'], GROUPS)
        self.assertEqual([int(x, 16) for x in data['irq']['group_masks']], MASKS)
        self.assertEqual([x - 32 for x in data['irq']['gic_intids']], data['irq']['dts_spi_indices'])

    def test_lan4_anchor_does_not_promote_unmeasured_candidates(self):
        data = json.loads((ROOT / 'docs/factory-ethernet-20260918.json').read_text())
        anchor = data['factory_port_anchor']
        self.assertEqual((anchor['panel_label'], anchor['sdk_port'], anchor['smac_index']), ('lan4', 3, 3))
        self.assertIsNone(anchor['serdes_mode'])
        self.assertFalse(anchor['modern_kernel_dma_verified'])
        self.assertFalse(data['unconfirmed_physical_sdk_candidates']['lan2']['verified_in_this_factory_capture'])

    def test_firmware_and_packet_evidence_keep_unknowns(self):
        data = json.loads((ROOT / 'docs/factory-ethernet-20260918.json').read_text())
        self.assertFalse(data['microcode']['apply_community_rx_hash_patches_to_backup_microcode'])
        self.assertFalse(data['microcode']['backup_bytes_equal_running_image_proven'])
        self.assertFalse(data['packet_baseline']['proves_raw_dma_payload_layout'])
        self.assertIsNone(data['protocol_configuration']['exact_queue_taken_by_captured_icmp'])
        self.assertFalse(data['binary_analysis']['proves_live_module_byte_identity'])

    def test_no_runtime_hardware_registration_or_factory_addresses(self):
        source = HEADER.read_text()
        self.assertNotRegex(source, r'\b(readl|writel|ioremap|register_netdev|dma_alloc_coherent)\s*\(')
        self.assertNotIn('0x80b33000', source)
        self.assertNotIn('0x19280000', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
