# SK-D840N IDM receive transport

## 2026-09-18 external build result

The user's `build-skd840n-idm-rx-kernel.log` compiles
`drivers/net/ethernet/zte/skd840n-idm-rx.o`, completes the final vmlinux link,
creates `arch/arm64/boot/Image`, and returns through the top-level make.
No C compilation or link error is reported. The only warning is an initial
targetinfo timestamp 0.18 seconds in the future; it did not abort this run.
This is external build evidence, not DMA, netdev or LAN4 traffic validation.
The log has no exact Git revision, final config, image hashes or shell exit
status. The reviewed development baseline is `52bb80fddddd240546b8b08da19bac314d385f92`.
See the latest [research entry](RESEARCH.md) for the log hash and limitations.
No rebuild, image boot or repeated PHY capture is needed for this documentation update.

## Status: kernel-build-integrated component, not a working Ethernet interface

This continues `2f43012679237023bb0092940864a3e403c46fa4` on the Ethernet
branch. `skd840n-idm-rx.c` now implements DMA allocation, descriptor polling,
copy-delivery, buffer refill and guarded resource destruction. Patch 150 and
`CONFIG_SKD840N_IDM_RX=y` put it in the kernel build for real API checking on
the user's build host. Local C compilation has NOT been performed.

There is no initcall, probe, compatible, IRQ request or netdev registration.
Merely compiling or booting the existing profiles does not call these APIs,
start new DMA, expose an eth0, or make LAN4 ping work. The component has no
complete NP/MAC parent yet. Do not use a dummy parent whose quiesce always
returns success, or add an initcall to publish buffers on inherited hardware.

LAN4 remains the first target: factory eth3 / SDK3 / SMAC3, combined with the
previous session's PHY 14f01000:0d. This work does not change those observations,
claim a new hardware result, or reclassify the unexplained responder at 0a.

## Evidence and the differences from the previous CPU-view helpers

The factory tar/report and previous immutable evidence remain the input basis.
The packet format/queue findings below are additional read-only analysis of
the backed-up np_133.ko, not quotations of conclusions in the supplied report.
Fingerprints and disassembly ranges are in `idm-rx-evidence.json`. No original
module, microcode, device identity or full factory capture is redistributed.

The anonymous function at .text+0x15c54 is connected to netif_napi_add through
relocations at 0xf3e0/0xf3f0 and the call at 0xf428. It is not the exported
idm_rx_test, even though a disassembler without local symbols labels it as an
offset from that preceding symbol. Its examined range ends at the next global
entry, 0x188f0; the range hash is not a claim of an ELF-declared function size.

| Observation | Implementation or remaining qualification |
|---|---|
| RX word0 is loaded natively on little-endian AArch64; metadata uses native byte/halfword access | Explicit `__le32` ring and `le32_to_cpu`; no guessed owner bit |
| normal RX takes buffer base then adds 0x80 at 0x1649c and 0x17744 | Copy from a tracked buffer plus 128, with frame bounds checked |
| RX BP initialization reverses each 32-bit address before storing | Separate `__be32` BP rings, not the descriptor endian convention |
| paired pending counts are at IDM+0xc4 through +0xf0 | 24 queues, each 16-bit half; reject counts above depth 1024 |
| idm_pkt_recv_update waits for +0x88 bit31 to clear | Retain pending credit while busy; do not silently lose release or refill twice |
| release command is count OR queue<<12 | Clear descriptor and order memory before issuing credit |
| idm_new_bp_update writes normal in low half and jumbo in high half at +0x100 | Publish chunks no larger than 1024; retain the original two domains |

All offsets are relative to the IDM resource, not absolute physical pointers.
The community driver's separate statistics-counter composition is not copied
as the pending-count API. Likewise its SR1010 switch/VLAN/queue-source mapping
is not applied to SK-D840N. The single-port debug TX path is not used as a
substitute for normal TX metadata or safe payload completion.

## Allocation and ownership

The new engine owns a 24x1024x32-byte coherent descriptor ring and coherent
normal/jumbo BP rings. 4096 normal and 128 jumbo buffers are individually
allocated and streaming-mapped with DMA_FROM_DEVICE, instead of copying the
factory's physical addresses or allocating one contiguous 0xee5000 block.
This is an integration design, not a tested SK-D840N allocation result.

The parent owns the DMA device; construction sets a 32-bit streaming/coherent
mask and validates the entire range of every allocation. Each returned DMA
base must match a software-owned mapping of the correct normal/jumbo pool.
Unknown addresses, duplicate held addresses and pool mismatches fault the
engine, mask RX interrupts and retain resources. It never casts a descriptor
address into a CPU pointer. The lookup cannot independently detect a hardware
replay after that same valid buffer has already been reposted; there is no
fabricated generation/cookie field in the descriptor.

Only normal frames of 14..2048 bytes without OMCI/reorder flags are delivered.
Jumbo and unsupported frames are counted as drops and recycled in their own
pool without reading payload. These are initial software admission policies,
not claims that other frame formats are invalid on the chip. The parent must
also configure hardware write bounds; a length check after DMA cannot prevent
an incorrectly configured engine from overrunning a buffer.

For accepted frames, the engine syncs the mapping for the CPU, calls a bounded
copy callback, then syncs it back for the device. The callback must copy before
returning and may not retain pointers. No checksum-offload assertion is made.
The descriptor is cleared before release; only after that command is accepted
is the buffer appended to the correct BP ring and its new credit published.
A busy release port retains one buffer/credit per queue; the frame callback is
not repeated on retries. dma_rmb/dma_wmb and ordered MMIO accesses are explicit.
The hardware acceptance/ordering assumptions still require actual testing.

One serialized poll owner visits all 24 queues with a rotating cursor and a
32-descriptor per-queue quantum. `poll(rx, budget, &more)` is NOT itself a NAPI
callback. `work < budget` with `more=true` still needs a retry. A future parent
must arrange bounded retries/backoff even if no fresh interrupt occurs; it
must not busy-spin forever or complete NAPI while silently stranding credits.

## Parent interface and safe-stop requirements

`alloc` performs memory/DMA API work only, with complete allocation rollback.
`layout` returns the new bases and geometry and marks them possibly exposed.
The future parent must establish exclusive NP/IDM ownership, turn on the
required clocks, stop legacy DMA, program all engine state while stopped and
set buffer-headroom/write limits before calling `publish` exactly once.
Returning addresses alone does not constitute engine initialization.

The parent must serialize APIs and callbacks and prevent IRQ/NAPI/work from
racing destruction. After any address exposure, `destroy` calls the mandatory
parent quiesce callback. That callback must handle partial setup, stop all
producers, mask/synchronize interrupts, and verify all DMA and posted accesses
are finished. Empty queue counts, IRQ masking and disabled DT nodes are NOT
hardware-stop proof. This callback has no board implementation in this commit.

If quiesce fails, destroy returns the error and keeps every mapping, allocation
and the handle. The parent must retain the device and MMIO resources too; it
cannot detach and pretend that leaking a pointer alone protects memory. A
failed stop cannot republish the initial credits. Destruction can be retried
after real recovery. Unexposed allocation rollback does not touch MMIO.

Still missing: full normal TX metadata and completion/payload-return ownership,
NP/PPU table and microcode initialization, SMAC/PHY configuration, actual
hardware quiescence, and the NAPI/IRQ/netdev parent. No PON, NAND, SMP or clock
policy changes are included. Those gaps are not bypassed by creating eth0.

## Validation and build-host check

25 new tests pass: 21 interpret selected actual C functions under explicit
MMIO/DMA/callback mocks, and 4 check source/build contracts. The previous 20
IDM-core tests also pass. Five injected regressions (BP endian, headroom,
free-on-stop-error, lost continuation flag, premature repost) are detected.
The interpreter is finite, single-owner and fail-closed on unknown syntax.
It does not execute the allocation/hash implementation, C ABI, real MMIO,
cache coherence, posted-bus latency or concurrent hardware. These are not
kernel or hardware tests. Missing pycparser skips interpreted tests explicitly.

Patch 150 is checked with zero fuzz/offset against exact leading excerpts of
the 6.12.103 Ethernet Kconfig/Makefile. This is not a full distribution patch
replay or make/Kconfig evaluation. Git whitespace checks exclude unified-patch context lines; actual added
patch lines are checked separately. Changed-file hashes are checked. No compiler, C preprocessor, dtc, make or hardware test ran locally.

The useful optional external check is compilation, not another PHY plug test.
Keep the proven ITB and existing feeds/config. After syncing this branch, the
build host can run the following Bash block; failure stops the sequence:

```bash
(
    set -e
    set -o pipefail
    make target/linux/clean
    make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-idm-rx-kernel.log
)
```

Expect `CC drivers/net/ethernet/zte/skd840n-idm-rx.o`. This is only a build
check; there is deliberately no new RAM image or ping procedure to test yet.
Do not rebuild feeds, weaken FAIL_ON_UNCONFIGURED/Werror, change FIT bounds,
write NAND, save U-Boot environment, or repeat the same PHY mapping session.
