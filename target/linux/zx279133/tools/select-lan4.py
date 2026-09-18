#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Select the RAM test profile without replacing packages/feeds or running make."""
import argparse
import datetime
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

PREFIX = 'CONFIG_TARGET_zx279133_generic_DEVICE_'
SELECTED = PREFIX + 'skyworth_sk-d840n-lan4'
RAM = {
    'CONFIG_TARGET_ROOTFS_INITRAMFS': 'y',
    'CONFIG_TARGET_INITRAMFS_COMPRESSION_NONE': 'y',
    'CONFIG_TARGET_INITRAMFS_FORCE': 'y',
    'CONFIG_TARGET_ROOTFS_INITRAMFS_SEPARATE': 'n',
    'CONFIG_TARGET_ALL_PROFILES': 'n',
}
SYMBOL = re.compile(r'^(?:# )?(CONFIG_[A-Za-z0-9_+.-]+)(?:=| is not set)')


def select(text: str) -> str:
    for required in ('CONFIG_TARGET_zx279133=y', 'CONFIG_TARGET_zx279133_generic=y'):
        if required not in text.splitlines():
            raise ValueError('existing .config must already select zx279133/generic')
    seen = set()
    out = []
    for line in text.splitlines():
        m = SYMBOL.match(line)
        key = m.group(1) if m else None
        if key and (key.startswith(PREFIX) or key in RAM):
            if key in seen:
                continue
            seen.add(key)
            value = 'y' if key == SELECTED else RAM.get(key, 'n')
            out.append(key + '=y' if value == 'y' else '# ' + key + ' is not set')
        else:
            out.append(line)
    for key, value in {SELECTED: 'y', **RAM}.items():
        if key not in seen:
            out.append(key + '=y' if value == 'y' else '# ' + key + ' is not set')
    return '\n'.join(out) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', nargs='?', type=Path, default=Path('.config'))
    args = parser.parse_args()
    path = args.config
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError('config must be an existing regular, non-symlink file')
        old = path.read_bytes()
        new = select(old.decode('utf-8')).encode('utf-8')
        if new == old:
            print('LAN4 profile already selected; no file changed')
            return 0
        backup = path.with_name(path.name + '.before-lan4-' +
                                datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        with backup.open('xb') as f:
            f.write(old)
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(backup, mode)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(new)
            os.chmod(temp, mode)
            os.replace(temp, path)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()
        print(f'Selected LAN4 RAM test; original configuration: {backup}')
        return 0
    except (OSError, UnicodeError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
