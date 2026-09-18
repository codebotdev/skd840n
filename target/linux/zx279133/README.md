# SK-D840N: LAN4 CPU-direct RAM experiment (recovery revision)

The current integrated experiment is documented in [LAN4-TEST.md](docs/LAN4-TEST.md).
Its interface is **lan4test**, its helper is **skd840n-lan4-test**, and it does
**not load microcode**. It is not the full normal NP/SE/PPU Ethernet driver.
No successful build, RAM boot, packet reception or ping is claimed for this
integrated revision. The earlier RX component has external build evidence only.

Delivery status: the connector blocked completion of the GitHub upload. The
remote recovery branch still points to the old baseline; use the supplied patch
kit, not a branch-only checkout, for this revision.

This recovery replaces previously announced download links whose files did not
exist. The prior claims of a microcode-containing kit, interface `lan4`, and
70 passing integrated tests were not supported and must not be used.

The original README is retained byte-for-byte in
[README-PRE-LAN4-RECOVERY.md](README-PRE-LAN4-RECOVERY.md).
[Research history and this revision](docs/RESEARCH.md) separate previous verified
results from the experimental integration. The baseline profiles are unchanged;
only the new `skyworth_sk-d840n-lan4` DTS instantiates this driver.

## Build host (not the device)

Preserve the known-good ITB, existing .config, feeds and toolchain. Synchronize
the baseline and apply the supplied recovery patch, then run from the complete
source root in Bash:

```bash
(
    set -e
    set -o pipefail
    python3 target/linux/zx279133/tools/select-lan4.py .config
    make defconfig
    make target/linux/clean
    make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-lan4-kernel.log
    make -j"$(nproc)" V=s 2>&1 | tee build-skd840n-lan4.log
)
```

The selector backs up .config and preserves package choices. Do not install any
firmware blob for this direct experiment. The expected output is
`bin/targets/zx279133/generic/immortalwrt-zx279133-generic-skyworth_sk-d840n-lan4-initramfs-fit.itb`.
Build failure stops the sequence; do not weaken configuration checks or Werror.

## Device test

Follow the complete [RAM boot and activation procedure](docs/LAN4-TEST.md).
Keep serial attached, use an isolated 1G peer on physical LAN4 only, and leave
other RJ45/optical ports disconnected during the experiment. The netdev is DOWN
until explicit activation. No NAND or persistent environment writes are needed.

```sh
skd840n-lan4-test status
skd840n-lan4-test start
# Continue only when activation succeeds and error=0.
ping -c 5 192.168.1.101
skd840n-lan4-test status
dmesg
```

The helper assigns 192.168.1.1/24 to **lan4test**, not lan4. Peer: 192.168.1.101/24.
TX is capped below ring depth to avoid reusing TX payload slots. Exposed memory
is retained on stop/failure because full hardware quiescence is not proved.
Cold power-cycle before another activation; do not test suspend, unbind, module
unload, warm restart, routing, throughput or long-running traffic.
