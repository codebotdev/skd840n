# LAN4 CPU-direct RAM experiment: recovery v1

## Read this first

This is an integrated **partially tested hardware experiment**, not a known-working
four-port firmware. The first user boot/activation reaches direct-ready and
reports PHY0d 1000/full. TX completed=13, RX consumed=0, and ARP/ping fail; wire
transmission is not established. See the latest RESEARCH.md entry. It registers `lan4test` DOWN and starts only through an
explicit `skd840n-lan4-test start` or `ip link set dev lan4test up`.
This revision uses CPU-direct/PPU-bypass operation and **no microcode**. Do not
follow the previous response's `lan4` name or microcode installation instruction.
The previous download files did not exist; this revision is the recoverable,
versioned replacement, with fresh tests and provenance.

The new C files are included in Kbuild through CONFIG_SKD840N_LAN4_TEST=y.
The new DTS alone instantiates them. The existing basic, MDIO-only and I/O
profiles do not start this hardware path. The first integrated FIT has boot
and activation evidence, but not successful Ethernet traffic. The serial log
has no exact Git revision, full build log or whole-FIT SHA256; it does not
validate every build input or prove the entire hardware recipe correct.

## Evidence and remaining assumptions

The prior user-labelled session maps LAN4 to MDIO 0x14f01000, PHY 0x0d,
ID 0x84b95032. The factory collection maps physical LAN4 to eth3/SDK3/SMAC3,
with 1000/full and a factory ICMP exchange. See factory-ethernet-20260918.json,
port-map-20260918.json, IDM-CORE.md and IDM-RX.md. These are earlier sources,
not new hardware observations from this recovery.

The recovered direct-TX code follows the earlier private-module analysis of
idm_net_lan_tx_direct: queue 2, 32-byte numeric descriptor, payload +128,
word1 inport/offset/length, word2 output port 3 and valid, word6 selector 0x1e.
This is a particular CPU-direct path, not a reconstruction of all normal TX
metadata. TM routines adapt the fixed cnjn reference at commit
07f8687248578d4be6931c665ff5d08bb6cc3d9d; no external-switch topology is imported.
Full normal NP/SE/PPU initialization and hardware quiescence are still separate
unfinished work. In particular, the CPU-direct RX source field and readiness
checks must be validated by this board, not inferred from the model.

NAPI services all 24 RX queues, using the existing RX component and a copy
callback. Four IRQ lines and a 20 ms delayed worker participate; this is not
pure polling. The worker handles TX completions, current PHY link and stalled
RX continuation. A 1G/full carrier is reported only when C22 state supports it;
there are no PHY data writes or synthetic fixed-link claims.

## Lifetime and test limits

This is a built-in, non-hotpluggable experiment. No remove/unbind/suspend or
normal warm-restart contract is provided. Hardware setup is attempted once
once DMA resources are exposed. Stop or initialization failure after exposure
retains memory, DMA mappings, pool, clock references and device resources.
Disabling interrupts, checking counters and known ingress gates is not proof
that all DMA has stopped. **Cold power-cycle before another test.**

Only 512 TX submissions are permitted, below ring depth 1024, so TX descriptor
and private payload slots are never reused in one experiment. A hardware
completion count is accounted as completion, not proof of successful wire
transmission. RX is stopped at a 512-consumed threshold checked after each
bounded poll; a final poll can exceed that threshold. Error -EDQUOT (-122)
after this cap is deliberate, not a reason to repeatedly bring the interface up.
No throughput, stress, routing, bridging or long-running traffic testing yet.

The coherent test pool is [0x94000000,0x98000000), 64 MiB, no-map/non-reusable.
It does not overlap WOE [0x91000000,0x93000000). The original dynamic PON reserve
is retained. Coherent BMU/rings/TX storage uses the DMA allocator; streaming RX
payloads use separately tracked mappings outside that pool. This is not an
IOMMU sandbox or protection against every stale/incorrect hardware write.

## Build in the existing full checkout

The recovery integration is published at 149bf05550dea8e8d1c0a7c74dfe704dcb1ef582
on skd840n/lan4-recovery-20260918. Synchronize that branch while preserving
local changes; do not apply the full recovery patch a second time. Preserve
the proven ITB and previous build inputs. The helper-only update needs no
kernel rebuild. For future kernel changes, use Bash from the repository root:

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

The selector requires an existing zx279133/generic configuration, creates a
mode-preserving backup, selects only skyworth_sk-d840n-lan4 and keeps package
choices. It does not run a compiler itself. Fresh workspaces can use
`docs/build-lan4.config` as the top-level seed; do not replace a configured
workspace with that seed unnecessarily. Do not rebuild feeds or weaken Werror,
FAIL_ON_UNCONFIGURED or Image/FIT size checks to force a result.

Expected file:

```
bin/targets/zx279133/generic/immortalwrt-zx279133-generic-skyworth_sk-d840n-lan4-initramfs-fit.itb
```

Record git rev-parse HEAD, .config, logs, FIT SHA256 and dumpimage -l output.
The existing rules retain load/entry 0x80000000, conf@133 and separate 32 MiB
bounds on Image memory occupancy and FIT length. No factory/sysupgrade image.
There is no firmware blob to install for this experiment.

## Cold boot via original U-Boot

Use an isolated test network. Keep TTL serial logging active. Interrupt original
autoboot; do not invoke zxboot, saveenv, NAND writes or bootloader replacement.
First transfer the complete FIT (the peer is 192.168.1.101/24):

```text
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.101
tftpboot 0x88000000 immortalwrt-zx279133-generic-skyworth_sk-d840n-lan4-initramfs-fit.itb
```

Only after successful transfer and matching byte count:

```text
setenv bootm_low 0x80000000
setenv bootm_size 0x10000000
setenv fdt_high 0x8fffffff
setenv bootargs 'console=ttyAMA0,115200n8 earlycon=zteuart,0x10d0d000 rdinit=/init maxcpus=1 clk_ignore_unused loglevel=8'
bootm 0x88000000#conf@133
```

Keep maxcpus=1, clk_ignore_unused and all original reservations. After TFTP and
RAM boot, connect the isolated 1G peer to physical LAN4 only; leave the other
RJ45/optical ports disconnected. Do not treat absence of optical support as
proof that inherited PON/WOE engines were already idle before the test.

## Activate and record one short test

First inspect the shell and DOWN interface:

```sh
ip link show
skd840n-lan4-test status
dmesg
```

The state must identify `version=lan4-direct-recovery-v1 interface=lan4test`,
`attempted=0`, `stage=registered-down`. If missing, collect the log; do not
invent an eth0 or apply UCI settings. When present, execute once:

```sh
skd840n-lan4-test start
```

The helper explicitly brings it up and assigns 192.168.1.1/24. Continue only
when that command succeeds and state reports error=0. Carrier can remain down
until negotiation; lack of carrier is not fixed by repeating activation.
With peer 192.168.1.101/24, run a small bidirectional test:

```sh
ping -I lan4test -c 3 -W 2 -w 10 192.168.1.101
ip neigh show dev lan4test
skd840n-lan4-test status
dmesg
```

Also ping 192.168.1.1 from the peer and capture ARP/ICMP on the connected peer
interface. A successful probe or TX counter alone is not Ethernet success.
Retain serial output from bootm, activation return/error, test_state before and
after, peer capture and FIT SHA256. Initialization failure, DMA errors or quota
exhaustion: save logs, do not run repeated up/down, cold power-cycle.

`skd840n-lan4-test stop` stops software and known ingress paths, retaining DMA
memory. It is NOT an assertion of full hardware stop or permission to warm boot.

## First zero-RX session: collect without rebuilding or reactivating

The old helper prints test_state and then aborts on unsupported ip -s. This
reporting defect does not cause the ARP failure. The revised helper uses
/sys/class/net/lan4test/statistics and only the BusyBox ip argument forms
available in the tested command family. It preserves failures and prints link,
IPv4 address, routes and neighbours. status is passive; rc=0 is not ping success.

For an already activated recovery-v1 image, these commands need no new helper,
package install or restart. First start capture on the peer's actual physical
LAN4-connected interface, filtering ARP and ICMP. Capture in the host OS owning
that NIC, not merely a VM/WSL virtual adapter. Keep the peer at 192.168.1.101/24.
Then, in the device's serial shell, run once:

```sh
echo 'CASE before-peer-capture'
cat /sys/class/net/lan4test/device/test_state
cat /proc/net/dev
ip addr show dev lan4test
ip route list
ip neigh show dev lan4test
ping -I lan4test -c 3 -W 2 -w 10 192.168.1.101
echo "ping_rc=$?"
echo 'CASE after-peer-capture'
cat /sys/class/net/lan4test/device/test_state
cat /proc/net/dev
ip neigh show dev lan4test
```

Use this only while running=1/error=0, not after a stop/fault. Do not issue
another start or up/down sequence. Also send one three-request ping from the
peer to 192.168.1.1 while capturing, then read device test_state once more.

No device ARP observed on the peer leaves TX/wiring/capture-point possibilities
open. Device ARP seen, followed by a peer reply actually transmitted while RX
stays zero, narrows the receiving-path investigation. Seeing an outgoing packet
in the peer's capture is not itself proof of arrival at the device. Do not
bypass ARP with a permanent neighbour or report TX completion as wire success.
Avoid flood tests because the 512-frame quota remains active.

Interface statistics ABI and TX-counter limits:
https://docs.kernel.org/6.12/networking/statistics.html
BusyBox ping interface/deadline options:
https://busybox.net/BusyBox.html

## Offline checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_lan4_recovery.py
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_idm_core.py
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_idm_rx.py
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_lan4_helper.py
sh -n target/linux/zx279133/base-files/usr/sbin/skd840n-lan4-test
```

Recovery run: 34 new + 20 core + 25 RX = 79 passing, no skips. pycparser is
required for the finite C models; no compiler or C preprocessor is invoked.
These are source/control-flow tests under mocks, not C ABI/type checking,
Kconfig evaluation, actual allocation/hash implementation, DMA/cache ordering,
concurrency, silicon simulation or hardware validation.
