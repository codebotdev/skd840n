/* SPDX-License-Identifier: GPL-2.0-only */
/* Arithmetic for the stock normal RX path; see docs/IDM-RX.md. */
#ifndef _SKD840N_IDM_RING_MATH_H
#define _SKD840N_IDM_RING_MATH_H

#include "skd840n-idm-core.h"

#define SKD840N_IDM_RX_COUNT_BASE 0x00c4U
#define SKD840N_IDM_RX_RELEASE    0x0088U
#define SKD840N_IDM_BP_REFILL     0x0100U
#define SKD840N_IDM_INT_MASK      0x0040U
#define SKD840N_IDM_RX_HEADROOM   128U
#define SKD840N_IDM_RX_FRAME_MAX  2048U
#define SKD840N_IDM_RX_QUANTUM    32U

static inline int skd840n_idm_count_reg(u32 queue, u32 *reg)
{
	if (!reg || queue >= SKD840N_IDM_RX_QUEUES)
		return -EINVAL;
	*reg = SKD840N_IDM_RX_COUNT_BASE + 4 * (queue >> 1);
	return 0;
}

/* A pending count, not a cumulative producer pointer or the statistics pair. */
static inline int skd840n_idm_count_value(u32 pair, u32 queue, u32 *count)
{
	u32 value;

	if (!count || queue >= SKD840N_IDM_RX_QUEUES)
		return -EINVAL;
	value = (pair >> (16 * (queue & 1))) & 0xffff;
	if (value > SKD840N_IDM_DESC_DEPTH)
		return -EOVERFLOW;
	*count = value;
	return 0;
}

static inline int skd840n_idm_release_word(u32 queue, u32 count, u32 *word)
{
	if (!word || queue >= SKD840N_IDM_RX_QUEUES ||
	    !count || count > SKD840N_IDM_DESC_DEPTH)
		return -EINVAL;
	*word = count | (queue << 12);
	return 0;
}

/* Normal low half, jumbo high half; 1024 is an actual supported chunk. */
static inline int skd840n_idm_refill_word(u32 normal, u32 jumbo, u32 *word)
{
	if (!word || (!normal && !jumbo) || normal > 1024 || jumbo > 1024)
		return -EINVAL;
	*word = normal | (jumbo << 16);
	return 0;
}

/* Stock buffer-base descriptors: data begins at base + 128, not at base. */
static inline int skd840n_idm_frame_range(u32 flags, u32 buffer_bytes,
				        u32 *length)
{
	u32 size = flags & SKD840N_RX_LENGTH_MASK;

	if (!length)
		return -EINVAL;
	/* Only normal, non-OMCI, non-reordered frames are admitted initially. */
	if (flags & (SKD840N_RX_ADDR_TYPE_MASK | SKD840N_RX_OMCI_MASK |
		     SKD840N_RX_REORDER_MASK))
		return -EINVAL;
	if (size < 14 || size > SKD840N_IDM_RX_FRAME_MAX ||
	    buffer_bytes < SKD840N_IDM_RX_HEADROOM ||
	    size > buffer_bytes - SKD840N_IDM_RX_HEADROOM)
		return -EMSGSIZE;
	*length = size;
	return 0;
}

/* Both the base (which must not be the empty marker) and the last byte fit. */
static inline int skd840n_idm_buffer_range(u64 dma, u32 bytes)
{
	if (!dma || !bytes || dma >= SKD840N_IDM_DMA32_LIMIT ||
	    bytes > SKD840N_IDM_DMA32_LIMIT - dma)
		return -ERANGE;
	return 0;
}

#endif /* _SKD840N_IDM_RING_MATH_H */
