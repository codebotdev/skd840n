/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _SKD840N_IDM_RX_H
#define _SKD840N_IDM_RX_H

#include <linux/dma-mapping.h>
#include <linux/types.h>

struct device;
struct skd840n_idm_rx;

struct skd840n_idm_rx_layout {
	dma_addr_t descriptors;
	dma_addr_t normal_bp;
	dma_addr_t jumbo_bp;
	u32 queues;
	u32 depth;
	u32 normal_depth;
	u32 jumbo_depth;
	u32 normal_count;
	u32 jumbo_count;
	u32 normal_bytes;
	u32 jumbo_bytes;
	u32 headroom;
};

struct skd840n_idm_rx_stats {
	u64 consumed;
	u64 delivered;
	u64 dropped;
	u64 release_busy;
	u64 not_ready;
	u64 faults;
};

/*
 * No platform driver or netdev is registered here. A future NP/MAC parent
 * must own the whole datapath and the IDM MMIO resource, stop legacy DMA,
 * configure the engine, and provide a real hardware-quiescence operation.
 *
 * All API calls and callbacks are serialized by ONE owner. Before destroy,
 * that owner must prevent new polls and synchronize IRQ/NAPI/work activity.
 * No callbacks may reenter this object. alloc/destroy are sleepable.
 *
 * receive() runs in the poll context, must not sleep and must COPY data
 * before returning; it may not retain data or words pointers. Return 0 for
 * an accepted frame. No checksum-offload claims or jack routing are made.
 *
 * quiesce() must disable new producers and wait for ALL accesses to these
 * buffers to stop, including posted bus writes. Masking IRQs, empty
 * counters, or disabled DT nodes are
 * NOT a sufficient implementation. On failure resources remain mapped;
 * parent must retain the device, MMIO mapping and handle, not detach. The
 * object cannot restart after a failed stop; destruction may be retried.
 */
struct skd840n_idm_rx_ops {
	int (*receive)(void *priv, const void *data, u32 len,
		       const u32 words[8], u32 queue);
	int (*quiesce)(void *priv);
};

struct skd840n_idm_rx *
skd840n_idm_rx_alloc(struct device *dev, void __iomem *idm,
		    const struct skd840n_idm_rx_ops *ops, void *priv);

/*
 * Copies ring addresses and marks them potentially visible to hardware.
 * Subsequent destruction requires successful quiesce even if setup fails.
 * Parent programs bases/depths ONLY while hardware is verified stopped.
 */
int skd840n_idm_rx_layout(struct skd840n_idm_rx *rx,
			struct skd840n_idm_rx_layout *layout);

/* Publish initial BP credits once, after parent has configured the engine. */
int skd840n_idm_rx_publish(struct skd840n_idm_rx *rx);

/*
 * Poll all 24 queues with a rotating start and bounded per-queue quantum.
 * Returns consumed descriptors (including drops), or a negative fault.
 * A fault may occur after earlier frames were delivered; stats retain them.
 * Busy release ports retain one pending credit/buffer per queue and retry
 * on the next poll. *more also covers the per-queue quantum and an
 * unpublished descriptor. This is NOT directly a NAPI poll return value:
 * work < budget does not imply idle when *more is true. Parent must arrange
 * bounded retries even without another IRQ (back off if no progress).
 */
int skd840n_idm_rx_poll(struct skd840n_idm_rx *rx, int budget, bool *more);
void skd840n_idm_rx_get_stats(struct skd840n_idm_rx *rx,
			     struct skd840n_idm_rx_stats *stats);

/* On quiesce failure returns error, keeps *rx valid and frees NOTHING. */
int skd840n_idm_rx_destroy(struct skd840n_idm_rx **rx);

#endif /* _SKD840N_IDM_RX_H */
