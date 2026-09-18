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
