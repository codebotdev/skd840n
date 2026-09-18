# SK-D840N research: LAN4 recovery integration

## Unmodified earlier history

[All entries through the external RX build](RESEARCH-PRE-LAN4-RECOVERY.md) are
retained byte-for-byte from Git blob
`b7c30b9d9fa8e8c62617ab62bfa0ce2e878b661d`, in the same directory so that its
relative references remain valid. Earlier history links inside that file are
also preserved. This is a continuation, not a replacement of earlier evidence.

## 2026-09-18: repair the undelivered LAN4 handoff

Baseline: `1ef3686aaeef3717bb8f8823aca002fba352409d`.
Recovery branch: `skd840n/lan4-recovery-20260918`.
The previous response announced a LAN4 patch, archive, microcode and 70-test
report without existing output files. Those delivery and test claims are
withdrawn. Source text was recovered from earlier generation records, reviewed
and revised; this is not a byte-identical resend of an existing old patch.

The recovered design is CPU-direct/PPU-bypass, with netdev **lan4test**, not the
previously described full microcode/SE-table data path. It does not request,
bundle, publish or load firmware. No binary module, flash dump, identity,
credential or raw factory capture is added to the repository.

### Code and revised safeguards

The integration adds the platform/netdev parent, C22 PHY0d read-only link polling,
SMAC3/TM/BMU/IDM startup, RX callback/NAPI/IRQ integration, and TX ring 2 using
private coherent bounce buffers. The dedicated profile and 64 MiB coherent pool
are separate from the unchanged baseline/MDIO/I/O DTSs. Probe publishes a DOWN
netdev; only explicit activation starts hardware. No UCI or upgrade path changes.

Compared with the recovered draft, this revision rejects all-ones readiness
values, completes fallible sysfs setup before netdev registration, and caps TX
at 512 frames, below the 1024-descriptor ring, so no TX slot is reused even if
the interpreted completion counter is not a full payload-lifetime guarantee.
The RX stop threshold is checked after a bounded poll and can be exceeded by
that poll's work. These are experimental safeguards, not DMA containment proof.

There is no proven full hardware-quiesce implementation. Once addresses can be
visible, faults/stops retain all allocations/mappings and the device lifetime.
The software masks interrupts and blocks known ingress paths, but does not
claim that every DMA master has stopped. Do not retry activation, unbind,
suspend, warm-reboot or reuse the memory; cold power-cycle for the next test.
Streaming RX payload buffers are NOT restricted to the dedicated coherent pool.

The clock/reset/CPU-direct/SMAC register recipes and source-port interpretation
still need hardware validation on this board. No SR1010 switch topology, PON
service, proprietary microcode, VLAN routing or acceleration is introduced.
The prior externally compiled RX component is reused without changes.

### Checks actually performed in this recovery

34 new offline checks pass: 7 descriptor/PHY-arithmetic tests and 17 transport
control-flow tests interpret selected actual C under explicit single-owner
kernel/MMIO mocks; 3 selector tests and 5 shell tests execute only local test
fixtures; 2 checks inspect source/build wiring. The 20 IDM-core and 25 RX tests
were rerun and pass: 79 total, no skips. This replaces, rather than confirms,
the unsupported previous 70-test statement.

The initial restored interpreter failed on header-guard parsing and scalar
pointer indexing; those model defects were corrected. One test originally
misclassified WRITE_ONCE(running) as a descriptor write; the event model now
distinguishes state and descriptor storage. No hardware evidence was invented
from those corrections. The model does not run kernel allocation/hash code,
prove C ABI/types, memory ordering, MMIO semantics, concurrency or silicon state.

No make, defconfig, compiler, preprocessor, dtc, firmware build or new device
execution is performed locally. [LAN4-TEST.md](LAN4-TEST.md) supplies the single
external build-to-boot workflow and failure logging. First-boot/ping success is
unknown, and four-port routing remains unimplemented.

### Delivery outcome

GitHub branch creation succeeded, but a subsequent source-blob upload was
blocked by the connector. No complete tree/commit was published. The remote
recovery branch remains at the baseline. Deliver this revision as the verified
patch/archive instead; do not treat that branch as an integrated test image.

## 2026-09-18: LAN4 boot/activation observed; fix BusyBox report failure

Reviewed repository baseline: `149bf05550dea8e8d1c0a7c74dfe704dcb1ef582`.
The recovery branch now contains the integration (the user published it);
the older delivery-failure entry above describes the earlier attempt only.
The new serial text is 36722 bytes, SHA256
`6b9620c02847bfb2184add828ae577faac0b38c16e1ed61787c7cfef33a47640`. Raw serial text and the runtime MAC are not committed.

### Hardware evidence, not inferred success

The supplied log validates FIT child hashes and boots the LAN4 CPU-direct DTS.
It creates the dedicated 64 MiB DMA pool and registers lan4test DOWN. After
correcting the old lan4 name, the user activates lan4test once. The driver prints
"direct test armed" and reports PHY0d 1000/full, with no initialization timeout
in this log. The final status reports direct-ready, running=1, error=0,
link_error=0, attempted=1, dma_retained=1, carrier=1; TX submitted/completed are
both 13, pending=0, drop=0. IRQ, RX consumed, delivered, drop, transport drop,
release-busy and not-ready are all zero. Five ping attempts have no reply;
the neighbour entry for 192.168.1.101 is FAILED. The rejected second start is
the intended one-activation guard, not another initialization failure.

This supports boot and activation, and a changing TX completion counter. It
DOES NOT prove wire transmission, reception, ARP success or ping. No peer-side
capture, exact firmware Git revision, final build config or whole-FIT SHA256
was supplied. Keep the source-port filter and hardware recipes unchanged;
there is not yet evidence identifying the failing MAC/DMA direction. Zero IRQ
alone is not an IRQ-root-cause diagnosis because the existing worker also
schedules RX polling. The earlier PHY mapping is not being requested again.

### Concrete software defect and fix

Both the manual ip -s command and the helper print BusyBox usage. The helper
then exits before its later address output. Replace its iproute2-only statistics
call with numeric sysfs statistics and BusyBox-compatible link/address/route/
neighbour commands. Keep a failed counter or command visible and continue the
other report sections; do not convert missing data into zero. Report success
means collection/driver-status success only, even with a FAILED neighbour.

Use an IPv4 address preflight before the one-shot activation, so an unsupported
query cannot silently turn into an address write after starting hardware.
Retain version, root and repeated-start guards. status never activates/stops,
restarts negotiation, sends packets, changes routes, flushes neighbours or
writes UCI/flash. Check the same printed test-state sample for error status.
The existing five shell-test fixtures now supply fake sysfs counters. Add 18
independent regression tests whose ip stub rejects unknown/unsupported flags.

### Validation and next test

18 new helper tests pass under both /bin/sh and local BusyBox 1.37.0 ash, and
all five pre-existing shell tests pass with the updated fixtures. These are
23 distinct tests (41 executions), not 41 independent test cases. The original
helper fails the new representative zero-RX/status regression on ip -s; the
fixed helper passes. Local BusyBox 1.37.0 also reproduces the unsupported ip -s
option. The fake ip/sysfs tests are not the target's complete BusyBox build or
real network tests. No C tests, compiler, make, dtc or new hardware run occurred.
Targeted source blobs and source-manifest hashes, shell syntax and whitespace
are checked; this is not a full checkout build/patch-chain replay.

Only the helper, its tests and documentation change. Existing C, DTS, Kconfig,
FIT rules, DMA lifetime/quotas and NAND protection are untouched. No kernel
rebuild is needed for this helper fix. Use the existing activated session,
record test_state and /proc/net/dev before/after one bounded interface-specific
ping, and capture ARP/ICMP on the peer's actual LAN4-connected NIC. Do not run
start again, add a static neighbour, weaken filters, or guess registers to
force link/traffic. Cold power-cycle only if a new activation is needed.
