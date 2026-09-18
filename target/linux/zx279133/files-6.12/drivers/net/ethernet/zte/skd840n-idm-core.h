/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * SK-D840N IDM CPU-view format and queue geometry, not a running DMA driver.
 *
 * Field positions were recovered from dump_desc_rx/dump_desc_tx in the
 * user's stock np_133.ko; provenance and limits: docs/IDM-CORE.md.
 * Inputs are NUMERIC CPU-view u32 words, as printed by those functions.
 * Do not cast a hardware ring to this view or infer DMA byte order from it.
 * No MMIO, memory allocation, ownership handoff or hardware activation here.
 */
#ifndef _SKD840N_IDM_CORE_H
#define _SKD840N_IDM_CORE_H

#include <linux/errno.h>
#include <linux/types.h>

#define SKD840N_IDM_WORDS                 8U
#define SKD840N_IDM_DESC_BYTES            32U
#define SKD840N_IDM_RX_QUEUES             24U
#define SKD840N_IDM_TX_QUEUES             4U
#define SKD840N_IDM_FREE_QUEUES           3U
#define SKD840N_IDM_EVENT_QUEUES          27U
#define SKD840N_IDM_IRQ_GROUPS            4U
#define SKD840N_IDM_DESC_DEPTH            1024U
#define SKD840N_IDM_RX_BP_DEPTH           8192U
#define SKD840N_IDM_RX_JUMBO_BP_DEPTH     4096U
#define SKD840N_IDM_TX_BP_DEPTH           1024U
#define SKD840N_IDM_FREE_DEPTH            65536U
#define SKD840N_IDM_BP_BYTES              4U
#define SKD840N_IDM_RX_BUFFERS            4096U
#define SKD840N_IDM_RX_JUMBO_BUFFERS      128U
#define SKD840N_IDM_TX_BUFFERS            1024U
#define SKD840N_IDM_BUFFER_BYTES          2304U
#define SKD840N_IDM_JUMBO_BYTES           16128U
#define SKD840N_IDM_REGIONS               9U
#define SKD840N_IDM_DMA32_LIMIT           0x100000000ULL

/* RX word 1; names follow the stock CPU-side dump, not a guessed owner bit. */
#define SKD840N_RX_LENGTH_MASK            0x00003fffU
#define SKD840N_RX_LENGTH_SHIFT           0U
#define SKD840N_RX_ADDR_TYPE_MASK         0x00004000U
#define SKD840N_RX_ADDR_TYPE_SHIFT        14U
#define SKD840N_RX_OMCI_MASK              0x00008000U
#define SKD840N_RX_OMCI_SHIFT             15U
#define SKD840N_RX_SOURCE_MASK            0x003f0000U
#define SKD840N_RX_SOURCE_SHIFT           16U
#define SKD840N_RX_CSUM_MASK              0x00400000U
#define SKD840N_RX_CSUM_SHIFT             22U
#define SKD840N_RX_OUTPUT_MASK            0x1f800000U
#define SKD840N_RX_OUTPUT_SHIFT           23U
#define SKD840N_RX_REORDER_MASK           0x20000000U
#define SKD840N_RX_REORDER_SHIFT          29U
/* RX word 2. */
#define SKD840N_RX_GEM_MASK               0x0000ffffU
#define SKD840N_RX_GEM_SHIFT              0U

/* TX word 1. */
#define SKD840N_TX_ADDR_TYPE_MASK         0x00000001U
#define SKD840N_TX_ADDR_TYPE_SHIFT        0U
#define SKD840N_TX_LENGTH_MASK            0x00007ffeU
#define SKD840N_TX_LENGTH_SHIFT           1U
#define SKD840N_TX_CSUM_MASK              0x00008000U
#define SKD840N_TX_CSUM_SHIFT             15U
#define SKD840N_TX_OFFSET_MASK            0x00ff0000U
#define SKD840N_TX_OFFSET_SHIFT           16U
#define SKD840N_TX_INPORT_MASK            0x3f000000U
#define SKD840N_TX_INPORT_SHIFT           24U
#define SKD840N_TX_IPV_MASK               0x40000000U
#define SKD840N_TX_IPV_SHIFT              30U
#define SKD840N_TX_L4_MASK                0x80000000U
#define SKD840N_TX_L4_SHIFT               31U
/* TX word 2. Bit 8 and bits 23..31 remain unclassified. */
#define SKD840N_TX_OMCI_MASK              0x00000001U
#define SKD840N_TX_OMCI_SHIFT             0U
#define SKD840N_TX_L3_OFFSET_MASK         0x000000feU
#define SKD840N_TX_L3_OFFSET_SHIFT        1U
#define SKD840N_TX_L4_OFFSET_MASK         0x0000fe00U
#define SKD840N_TX_L4_OFFSET_SHIFT        9U
#define SKD840N_TX_OUTPORT_MASK           0x003f0000U
#define SKD840N_TX_OUTPORT_SHIFT          16U
#define SKD840N_TX_OUT_VALID_MASK         0x00400000U
#define SKD840N_TX_OUT_VALID_SHIFT        22U
/* TX word 6. Do not invent meanings for its high seven bits. */
#define SKD840N_TX_GEM_MASK               0x0000ffffU
#define SKD840N_TX_GEM_SHIFT              0U
#define SKD840N_TX_QUEUE_MASK             0x01ff0000U
#define SKD840N_TX_QUEUE_SHIFT            16U

struct skd840n_idm_field {
	u32 word;
	u32 mask;
	u32 shift;
};

/* Stable field order for the offline inspector and a future driver. */
static const struct skd840n_idm_field skd840n_idm_rx_fields[] = {
	{ 0, 0xffffffffU, 0 }, /* address: interpretation depends on addr_type */
	{ 1, SKD840N_RX_LENGTH_MASK, SKD840N_RX_LENGTH_SHIFT },
	{ 1, SKD840N_RX_ADDR_TYPE_MASK, SKD840N_RX_ADDR_TYPE_SHIFT },
	{ 1, SKD840N_RX_OMCI_MASK, SKD840N_RX_OMCI_SHIFT },
	{ 1, SKD840N_RX_SOURCE_MASK, SKD840N_RX_SOURCE_SHIFT },
	{ 1, SKD840N_RX_CSUM_MASK, SKD840N_RX_CSUM_SHIFT },
	{ 1, SKD840N_RX_OUTPUT_MASK, SKD840N_RX_OUTPUT_SHIFT },
	{ 1, SKD840N_RX_REORDER_MASK, SKD840N_RX_REORDER_SHIFT },
	{ 2, SKD840N_RX_GEM_MASK, SKD840N_RX_GEM_SHIFT },
};

static const struct skd840n_idm_field skd840n_idm_tx_fields[] = {
	{ 0, 0xffffffffU, 0 },
	{ 1, SKD840N_TX_LENGTH_MASK, SKD840N_TX_LENGTH_SHIFT },
	{ 1, SKD840N_TX_ADDR_TYPE_MASK, SKD840N_TX_ADDR_TYPE_SHIFT },
	{ 1, SKD840N_TX_OFFSET_MASK, SKD840N_TX_OFFSET_SHIFT },
	{ 1, SKD840N_TX_CSUM_MASK, SKD840N_TX_CSUM_SHIFT },
	{ 1, SKD840N_TX_INPORT_MASK, SKD840N_TX_INPORT_SHIFT },
	{ 1, SKD840N_TX_IPV_MASK, SKD840N_TX_IPV_SHIFT },
	{ 1, SKD840N_TX_L4_MASK, SKD840N_TX_L4_SHIFT },
	{ 2, SKD840N_TX_OMCI_MASK, SKD840N_TX_OMCI_SHIFT },
	{ 2, SKD840N_TX_L3_OFFSET_MASK, SKD840N_TX_L3_OFFSET_SHIFT },
	{ 2, SKD840N_TX_L4_OFFSET_MASK, SKD840N_TX_L4_OFFSET_SHIFT },
	{ 2, SKD840N_TX_OUT_VALID_MASK, SKD840N_TX_OUT_VALID_SHIFT },
	{ 2, SKD840N_TX_OUTPORT_MASK, SKD840N_TX_OUTPORT_SHIFT },
	{ 6, SKD840N_TX_GEM_MASK, SKD840N_TX_GEM_SHIFT },
	{ 6, SKD840N_TX_QUEUE_MASK, SKD840N_TX_QUEUE_SHIFT },
};

/* Accept only a nonempty contiguous u32 field in one of eight words. */
static inline int skd840n_idm_field_valid(u32 word, u32 mask, u32 shift)
{
	u32 value;

	if (word >= SKD840N_IDM_WORDS || !mask || shift >= 32)
		return 0;
	if (mask & ((1U << shift) - 1))
		return 0;
	value = mask >> shift;
	return !(value & (value + 1));
}

static inline int skd840n_idm_get_field(const u32 *words, u32 count,
				       u32 word, u32 mask, u32 shift,
				       u32 *value)
{
	if (!words || !value)
		return -EINVAL;
	if (count != SKD840N_IDM_WORDS)
		return -EMSGSIZE;
	if (!skd840n_idm_field_valid(word, mask, shift))
		return -EINVAL;
	*value = (words[word] & mask) >> shift;
	return 0;
}

/*
 * Edit a known CPU-view field, retaining every other bit. This does NOT
 * prepare a complete transmit descriptor: unknown metadata, DMA ordering,
 * buffer ownership and the selected ring must be established by the caller.
 */
static inline int skd840n_idm_set_field(u32 *words, u32 count, u32 word,
				       u32 mask, u32 shift, u32 value)
{
	if (!words)
		return -EINVAL;
	if (count != SKD840N_IDM_WORDS)
		return -EMSGSIZE;
	if (!skd840n_idm_field_valid(word, mask, shift))
		return -EINVAL;
	if (value > (mask >> shift))
		return -ERANGE;
	words[word] = (words[word] & ~mask) | (value << shift);
	return 0;
}

/*
 * Region order is CPU RX desc, CPU TX desc, RX BP, RX jumbo BP, TX BP,
 * TX free rings, RX payload, RX jumbo payload, TX payload. Numeric sizes
 * match the supplied factory configuration, but contain no runtime address.
 */
static const u32 skd840n_idm_region_bytes[SKD840N_IDM_REGIONS] = {
	SKD840N_IDM_RX_QUEUES * SKD840N_IDM_DESC_DEPTH * SKD840N_IDM_DESC_BYTES,
	SKD840N_IDM_TX_QUEUES * SKD840N_IDM_DESC_DEPTH * SKD840N_IDM_DESC_BYTES,
	SKD840N_IDM_RX_BP_DEPTH * SKD840N_IDM_BP_BYTES,
	SKD840N_IDM_RX_JUMBO_BP_DEPTH * SKD840N_IDM_BP_BYTES,
	SKD840N_IDM_TX_BP_DEPTH * SKD840N_IDM_BP_BYTES,
	SKD840N_IDM_FREE_QUEUES * SKD840N_IDM_FREE_DEPTH * SKD840N_IDM_BP_BYTES,
	SKD840N_IDM_RX_BUFFERS * SKD840N_IDM_BUFFER_BYTES,
	SKD840N_IDM_RX_JUMBO_BUFFERS * SKD840N_IDM_JUMBO_BYTES,
	SKD840N_IDM_TX_BUFFERS * SKD840N_IDM_BUFFER_BYTES,
};

/*
 * Plan a DMA32 region without allocating/mapping it. dma is an address from
 * a future DMA API allocation, NOT virt_to_phys(), DT reserved memory or an
 * address copied from a factory log. Outputs remain untouched on failure.
 */
static inline int skd840n_idm_plan(u64 dma, u64 bytes, u32 *addresses,
				   u32 count, u32 *used)
{
	u32 total = 0;
	u32 i;

	if (!addresses || !used || count != SKD840N_IDM_REGIONS)
		return -EINVAL;
	if (dma & 0xfff)
		return -EINVAL;
	for (i = 0; i < SKD840N_IDM_REGIONS; i++)
		total += skd840n_idm_region_bytes[i];
	if (bytes < total)
		return -ENOSPC;
	if (dma >= SKD840N_IDM_DMA32_LIMIT ||
	    total > SKD840N_IDM_DMA32_LIMIT - dma)
		return -ERANGE;
	total = 0;
	for (i = 0; i < SKD840N_IDM_REGIONS; i++) {
		addresses[i] = dma + total;
		total += skd840n_idm_region_bytes[i];
	}
	*used = total;
	return 0;
}

/* Values 0..3 select IRQ GROUPS, not Linux CPU numbers. */
static inline int skd840n_idm_irq_masks(const u32 *groups, u32 count,
					u32 *masks)
{
	u32 result[SKD840N_IDM_IRQ_GROUPS] = { 0, 0, 0, 0 };
	u32 i;

	if (!groups || !masks || count != SKD840N_IDM_EVENT_QUEUES)
		return -EINVAL;
	for (i = 0; i < count; i++) {
		if (groups[i] >= SKD840N_IDM_IRQ_GROUPS)
			return -EINVAL;
		result[groups[i]] |= 1U << i;
	}
	for (i = 0; i < SKD840N_IDM_IRQ_GROUPS; i++)
		masks[i] = result[i];
	return 0;
}

/* All RX queues, including trap qid 6 and the non-CPU-labelled banks. */
static inline u32 skd840n_idm_rx_events(u32 events)
{
	return events & ((1U << SKD840N_IDM_RX_QUEUES) - 1);
}

/*
 * CPU view of one outport-to-real-port table word (stock ERAM table 0x27).
 * This table's real_queue is NOT a CPU RX qid or an IDM TX ring index.
 * Only update documented fields; the caller owns the other table bits.
 */
static inline int skd840n_idm_route_word(u32 original, u32 real_port,
				       u32 real_queue, u32 *updated)
{
	if (!updated)
		return -EINVAL;
	if (real_port > 63 || real_queue > 511)
		return -ERANGE;
	*updated = (original & ~0x7fffU) | real_port | (real_queue << 6);
	return 0;
}

/* A wrapped completion count may not free more buffers than are owned. */
static inline int skd840n_idm_completed16(u32 now, u32 before,
					u32 outstanding, u32 *completed)
{
	u32 delta;

	if (!completed || now > 0xffff || before > 0xffff ||
	    outstanding >= 0x10000)
		return -EINVAL;
	delta = (now - before) & 0xffff;
	if (delta > outstanding)
		return -EOVERFLOW;
	*completed = delta;
	return 0;
}

#endif /* _SKD840N_IDM_CORE_H */
