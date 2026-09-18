#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the generic deferred-MTD hook and the target's disabled-block stub.

No compiler, C preprocessor, make, toolchain probe or hardware access is used.
The real generic header hunk and target patch are applied to an exact excerpt
of Linux v6.12.103 include/linux/mtd/blktrans.h (original lines 76-81, Git blob
6e471436bba5564c040e26b131e8a01f028e8759). A deliberately limited interpreter
selects only #if IS_ENABLED(CONFIG_...), #else and #endif in that hook region.
This checks declarations and stub contents, not code generation or linking.
In particular, retaining the m case does NOT validate modular MTD operation.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC = ROOT.parent / 'generic/hack-6.12/402-mtd-blktrans-call-add-disks-after-mtd-device.patch'
FIX = ROOT / 'patches-6.12/140-mtd-blktrans-stub-when-disabled.patch'
HEADER = 'include/linux/mtd/blktrans.h'
HOOK = 'register_mtd_blktrans_devs'
EXTERN = f'extern void {HOOK}(void);'
# Exact upstream text. Blank prefix preserves the real hunk line numbers.
UPSTREAM = '\n' * 75 + '''extern int add_mtd_blktrans_dev(struct mtd_blktrans_dev *dev);
extern int del_mtd_blktrans_dev(struct mtd_blktrans_dev *dev);
extern int mtd_blktrans_cease_background(struct mtd_blktrans_dev *dev);

/**
 * module_mtd_blktrans() - Helper macro for registering a mtd blktrans driver
'''


def header_hunk(patch):
    start = patch.index('--- a/' + HEADER + '\n')
    end = patch.find('\n--- a/', start + 1)
    return patch[start:] if end < 0 else patch[start:end + 1]


def apply_patch(directory, text):
    result = subprocess.run(
        ['patch', '-p1', '--batch', '--forward', '--fuzz=0'],
        cwd=directory, input=text, text=True, capture_output=True, check=False)
    if result.returncode or re.search(r'offset|fuzz', result.stdout + result.stderr):
        raise AssertionError('patch did not apply exactly:\n' + result.stdout + result.stderr)


def hook_region(header):
    marker = 'extern int mtd_blktrans_cease_background(struct mtd_blktrans_dev *dev);'
    return header.split(marker, 1)[1].split('/**', 1)[0]


def select_hook(region, defined):
    """Interpret this small conditional region; reject unknown directives."""
    output = []
    active = True
    branch = None
    seen_else = False
    for line in region.splitlines():
        text = line.strip()
        if text.startswith('#'):
            match = re.fullmatch(r'#if IS_ENABLED\((CONFIG_\w+)\)', text)
            if match:
                if branch is not None:
                    raise ValueError('nested condition outside this model')
                symbol = match.group(1)
                branch = defined.get(symbol) == 1 or defined.get(symbol + '_MODULE') == 1
                active = branch
                seen_else = False
            elif text == '#else' and branch is not None and not seen_else:
                active = not branch
                seen_else = True
            elif text == '#endif' and branch is not None:
                active = True
                branch = None
            else:
                raise ValueError('unsupported directive: ' + text)
        elif active:
            output.append(line)
    if branch is not None:
        raise ValueError('unterminated conditional')
    return '\n'.join(output).strip()


class BlktransTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('patch'):
            raise RuntimeError('patch is required; no tests may be silently skipped')
        cls.generic = GENERIC.read_text(encoding='utf-8')
        cls.fix = FIX.read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory(prefix='skd840n-mtd-') as tmp:
            path = Path(tmp) / HEADER
            path.parent.mkdir(parents=True)
            path.write_text(UPSTREAM, encoding='utf-8')
            apply_patch(tmp, header_hunk(cls.generic))
            cls.before = path.read_text(encoding='utf-8')
            apply_patch(tmp, cls.fix)
            cls.after = path.read_text(encoding='utf-8')
        cls.region = hook_region(cls.after)

    def test_old_disabled_case_requires_missing_external_hook(self):
        self.assertEqual(select_hook(hook_region(self.before), {}), EXTERN)
        self.assertNotEqual(select_hook(self.region, {}), EXTERN)

    def test_disabled_case_is_empty_inline_not_external_reference(self):
        result = select_hook(self.region, {})
        self.assertNotIn('extern', result)
        self.assertRegex(result, r'^static inline void ' + HOOK + r'\(void\)\s*\{\s*\}$')
        self.assertEqual(result.count(HOOK), 1)

    def test_builtin_case_preserves_external_hook(self):
        self.assertEqual(select_hook(self.region, {'CONFIG_MTD_BLKDEVS': 1}), EXTERN)

    def test_module_case_preserves_existing_declaration_only(self):
        self.assertEqual(select_hook(self.region, {'CONFIG_MTD_BLKDEVS_MODULE': 1}), EXTERN)

    def test_guard_is_translation_layer_not_individual_block_driver(self):
        for irrelevant in ('CONFIG_MTD', 'CONFIG_MTD_BLOCK', 'CONFIG_MTD_BLOCK_RO'):
            with self.subTest(irrelevant=irrelevant):
                self.assertEqual(select_hook(self.region, {irrelevant: 1}),
                                 select_hook(self.region, {}))
                self.assertEqual(select_hook(self.region,
                    {'CONFIG_MTD_BLKDEVS': 1, irrelevant: 0}), EXTERN)

    def test_no_change_to_other_header_declarations(self):
        self.assertEqual(self.after.replace(hook_region(self.after), '\n'),
                         self.before.replace(hook_region(self.before), '\n'))
        self.assertEqual(re.findall(r'^\+\+\+ b/(.+)$', self.fix, re.M), [HEADER])
        self.assertEqual(re.findall(r'^--- a/(.+)$', self.fix, re.M), [HEADER])

    def test_matches_actual_generic_call_and_provider(self):
        self.assertIn('+\t' + HOOK + '();\n', self.generic)
        provider = self.generic.split('--- a/drivers/mtd/mtd_blkdevs.c\n', 1)[1]
        provider = provider.split('--- a/drivers/mtd/mtdcore.c\n', 1)[0]
        self.assertIn('+void ' + HOOK + '(void)\n+{', provider)

    def test_target_keeps_mtd_character_access_and_block_interfaces_disabled(self):
        config = (ROOT / 'config-6.12').read_text(encoding='utf-8')
        for symbol in ('MTD_BLOCK', 'MTD_BLOCK_RO', 'MTD_UBI', 'SPI_SPIDEV'):
            self.assertRegex(config, r'(?m)^# CONFIG_' + symbol + r' is not set$')
        for symbol in ('MTD', 'MTD_SPI_NAND', 'SPI_SKD840N_RO', 'SKD840N_MDIO_DIAG'):
            self.assertRegex(config, r'(?m)^CONFIG_' + symbol + r'=y$')

    def test_interpreter_fails_closed_on_unsupported_directives(self):
        for text in ('#if UNKNOWN\n#endif', '#else', '#if IS_ENABLED(CONFIG_A)',
                     '#if IS_ENABLED(CONFIG_A)\n#if IS_ENABLED(CONFIG_B)\n#endif\n#endif'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                select_hook(text, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
