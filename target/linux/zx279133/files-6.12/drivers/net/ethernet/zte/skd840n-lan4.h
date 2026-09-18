/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _SKD840N_LAN4_H
#define _SKD840N_LAN4_H

#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/types.h>
#include "skd840n-idm-rx.h"
#include "skd840n-lan4-format.h"

#define SKD840N_NPPT_BYTES 0x01000000U
#define SKD840N_IDM_OFFSET 0x00280000U
#define SKD840N_SMAC3_OFFSET 0x00100000U
#define SKD840N_BMU_BYTES 0x01f60000U
#define SKD840N_BMU_COUNT 0x3400U
#define SKD840N_BMU_NORMAL_BYTES 0x900U
#define SKD840N_BMU_JUMBO_BYTES 0x3f00U
#define SKD840N_FREE_BYTES (3U * 65536U * 4U)
#define SKD840N_TX_DESC_BYTES (4U * 1024U * 32U)
#define SKD840N_TX_DATA_BYTES (1024U * SKD840N_DIRECT_TX_STRIDE)
#define SKD840N_IDM_RX_MASK 0x00ffffffU
#define SKD840N_IDM_ALL_MASK 0x07ffffffU
#define SKD840N_IDM_STATUS 0x3cU
#define SKD840N_IDM_MASK 0x40U
#define SKD840N_TX_DOORBELL 0xa4U
#define SKD840N_TX_DONE 0xb0U

struct skd840n_lan4_hw {
	struct device *dev;
	void __iomem *np;
	void __iomem *pps;
	void *bmu;
	dma_addr_t bmu_dma;
	dma_addr_t tx_dma;
	dma_addr_t free_dma;
	struct skd840n_idm_rx_layout rx;
	const char *stage;
	u32 wait_offset;
	u32 wait_value;
};

/* These operations do not release any DMA memory or certify quiescence. */
void skd840n_lan4_block(struct skd840n_lan4_hw *h);
void skd840n_lan4_gate(struct skd840n_lan4_hw *h, bool enabled);
int skd840n_lan4_prepare(struct skd840n_lan4_hw *h);
int skd840n_lan4_enable(struct skd840n_lan4_hw *h);
#endif
