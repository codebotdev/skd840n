/* SPDX-License-Identifier: GPL-2.0-only */
/* CPU-direct test format recovered from stock idm_net_lan_tx_direct(). */
#ifndef _SKD840N_LAN4_FORMAT_H
#define _SKD840N_LAN4_FORMAT_H
#include "skd840n-idm-core.h"

#define SKD840N_DIRECT_PORT 3U
#define SKD840N_DIRECT_PHY 13U
#define SKD840N_DIRECT_PHY_ID 0x84b95032U
#define SKD840N_DIRECT_QUEUE 2U
#define SKD840N_DIRECT_FRAME_MAX 1518U
#define SKD840N_DIRECT_HEADROOM 128U
#define SKD840N_DIRECT_TX_DEPTH 1024U
#define SKD840N_DIRECT_TX_STRIDE 2304U

/* Numeric CPU words, serialized with cpu_to_le32() by the producer. */
static inline int skd840n_direct_tx_words(u64 dma, u32 len, u32 words[8])
{
	u32 i;

	if (!words || len < 60 || len > SKD840N_DIRECT_FRAME_MAX)
		return -EINVAL;
	if (!dma || dma >= SKD840N_IDM_DMA32_LIMIT ||
	    SKD840N_DIRECT_TX_STRIDE > SKD840N_IDM_DMA32_LIMIT - dma)
		return -ERANGE;
	for (i = 0; i < 8; i++)
		words[i] = 0;
	words[0] = (u32)dma;
	words[1] = 0x0f800000U | (len << 1);
	words[2] = 0x00430000U;
	/* Stock direct path's selector. NOT a guessed queue or owner bit. */
	words[6] = 0x1e000000U;
	return 0;
}

/* No completion clamping: impossible deltas must quarantine DMA memory. */
static inline int skd840n_direct_completed(u32 now, u32 before, u32 pending,
					 u32 *completed)
{
	u32 delta;

	if (!completed || pending >= SKD840N_DIRECT_TX_DEPTH ||
	    now > 65535 || before > 65535)
		return -EINVAL;
	delta = (now - before) & 0xffff;
	if (delta > pending)
		return -EOVERFLOW;
	*completed = delta;
	return 0;
}

/* Clause 22 standard ability resolution; never infer AN speed from BMCR. */
static inline int skd840n_direct_gigabit(u32 bmcr, u32 bmsr,
				       u32 advertise, u32 partner)
{
	if (bmcr == 0xffff || bmsr == 0xffff ||
	    advertise == 0xffff || partner == 0xffff)
		return -ENODATA;
	if (!(bmsr & 4) || bmcr & 0xcc00)
		return 0;
	if (bmcr & 0x1000)
		return !!((bmsr & 0x20) && (advertise & 0x0200) &&
			  (partner & 0x0800) && !(partner & 0x8000));
	return (bmcr & 0x2140) == 0x0140;
}
#endif
