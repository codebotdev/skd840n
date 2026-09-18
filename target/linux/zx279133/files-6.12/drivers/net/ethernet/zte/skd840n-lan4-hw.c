// SPDX-License-Identifier: GPL-2.0-only
/*
 * SK-D840N CPU-direct experiment. Stock direct-through and SMAC/IDM analysis:
 * docs/LAN4-TEST.md and docs/lan4-direct-evidence.json.
 * Shared TM sequences adapted from cnjn/zx279133-tm.c, commit
 * 07f8687248578d4be6931c665ff5d08bb6cc3d9d (GPL-2.0-only).
 * No PPS microcode, SR1010 topology, SerDes recipe or offload is imported.
 */
#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/iopoll.h>
#include "skd840n-lan4.h"

static void change(struct skd840n_lan4_hw *h, u32 reg, u32 mask, u32 value)
{
	writel((readl(h->np + reg) & ~mask) | (value & mask), h->np + reg);
}

static int wait_bits(struct skd840n_lan4_hw *h, u32 reg, u32 mask, u32 want)
{
	u32 v;
	int ret;

	ret = readl_poll_timeout(h->np + reg, v,
				v != ~0U && (v & mask) == want, 10, 100000);
	if (ret) {
		h->wait_offset = reg;
		h->wait_value = v;
		dev_err(h->dev, "%s timeout: NPPT+%#x=%#x\n", h->stage, reg, v);
	}
	return ret;
}

void skd840n_lan4_gate(struct skd840n_lan4_hw *h, bool enabled)
{
	change(h, SKD840N_SMAC3_OFFSET, 3, enabled ? 3 : 0);
	readl(h->np + SKD840N_SMAC3_OFFSET);
}

void skd840n_lan4_block(struct skd840n_lan4_hw *h)
{
	u32 i;

	writel(SKD840N_IDM_ALL_MASK, h->np + SKD840N_IDM_OFFSET + SKD840N_IDM_MASK);
	change(h, 0x10000, BIT(3), 0);
	change(h, 0x4000, BIT(0), 0);
	for (i = 0; i < 4; i++)
		change(h, 0x40000 * (i + 1), 3, 0);
	readl(h->np + 0x4000);
}

static int reset_path(struct skd840n_lan4_hw *h)
{
	u32 v, q;
	int ret;

	h->stage = "handoff-ppu-drain";
	skd840n_lan4_block(h);
	/* Stock normal2p1: stop ISU output, enable debug bypass, wait PPU empty. */
	change(h, 0x10000, BIT(2), BIT(2));
	ret = readl_poll_timeout(h->pps + 0x80014, v, v != ~0U && (v & 1),
				10, 100000);
	if (ret) {
		h->wait_offset = 0x80014;
		h->wait_value = v;
		dev_err(h->dev, "PPU drain timeout, PPS+0x80014=%#x\n", v);
		return ret;
	}
	h->stage = "nppt-reset";
	change(h, 0x2c0004, BIT(4), 0);
	usleep_range(400, 500);
	change(h, 0x2c0004, BIT(31), 0);
	usleep_range(400, 500);
	change(h, 0x2c0004, BIT(31), BIT(31));
	ret = wait_bits(h, 0x80, 0x1fd, 0x1fd);
	if (ret)
		return ret;
	usleep_range(200, 300);
	change(h, 0x2c0004, BIT(4), BIT(4));
	skd840n_lan4_block(h);
	/* Refuse to put fresh rings on a live, non-reset queue state. */
	h->stage = "idm-reset-counters";
	for (q = 0; q < 12; q++) {
		ret = wait_bits(h, SKD840N_IDM_OFFSET + 0xc4 + 4 * q, ~0U, 0);
		if (ret)
			return ret;
	}
	for (q = 0; q < 4; q++) {
		u32 reg = q ? 4 * (q + 42) : 0x84;

		ret = wait_bits(h, SKD840N_IDM_OFFSET + reg, 0xffff, 0);
		if (ret)
			return ret;
	}
	return 0;
}

static void bmu_prepare(struct skd840n_lan4_hw *h)
{
	__be16 *bp = h->bmu;
	u32 base = lower_32_bits(h->bmu_dma);
	u32 end = base + 0x20000 + SKD840N_BMU_COUNT * SKD840N_BMU_NORMAL_BYTES;
	u32 i;

	writel(0, h->np + 0x3c000);
	for (i = 0; i < SKD840N_BMU_COUNT; i++)
		bp[i] = cpu_to_be16(i);
	dma_wmb();
	writel(0x0104c040, h->np + 0x3c004);
	writel(0x0104c040, h->np + 0x3c008);
	writel(base, h->np + 0x54);
	writel(base + 0x10000, h->np + 0x58);
	writel(end, h->np + 0x5c);
	writel(base + 0x20000, h->np + 0x60);
	writel(end, h->np + 0x64); /* No BMU jumbo credits are published. */
	writel(SKD840N_BMU_NORMAL_BYTES | (SKD840N_BMU_JUMBO_BYTES << 16), h->np + 0x68);
	writel(0x801, h->np + 0x6c);
	writel(SKD840N_BMU_COUNT << 16, h->np + 0x3c048);
	writel(0, h->np + 0x3c04c);
	writel((SKD840N_BMU_COUNT >> 5) - 1, h->np + 0x3c058);
	writel(~0U, h->np + 0x3c05c); /* Stock zero-jumbo upper-index sentinel. */
	writel(SKD840N_BMU_COUNT << 16, h->np + 0x3c060);
	writel(0, h->np + 0x3c064);
	writel(SKD840N_BMU_COUNT, h->np + 0x20030);
	writel(0, h->np + 0x20034);
	writel(0x801, h->np + 0x20078);
	writel(1, h->np + 0x3c000);
}

static int red_write(struct skd840n_lan4_hw *h, u32 ram, u32 addr,
		     const u32 *data, u32 words)
{
	u32 gate = readl(h->np + 0xb8);
	int ret;

	change(h, 0xb8, BIT(10), 0);
	ret = wait_bits(h, 0x20018, 1, 1);
	if (ret)
		goto out;
	writel(addr | (ram << 22), h->np + 0x20014);
	while (words) {
		words--;
		writel(data[words], h->np + 0x2001c + 4 * words);
	}
	ret = wait_bits(h, 0x20018, 1, 1);
out:
	writel(gate, h->np + 0xb8);
	return ret;
}

static int tm_prepare(struct skd840n_lan4_hw *h)
{
	static const u32 cfg[4] = { 0xff803fff, 0x0100ff80, 0x00100200, 0x20 };
	static const struct { u32 reg, mask, value; } regs[] = {
		{ 0x38000, BIT(21), 0 },
		{ 0x38398, 0x7c00, 8 << 10 },
		{ 0x20088, 0xffff0000, 0x5e000000 },
		{ 0x24018, 0x8000ffff, 0x80004e00 },
		{ 0x24020, 0x8000ffff, 0x80000fa0 },
		{ 0x343c4, 0x3fff, 0 },
		{ 0x343d0, 0x1f1f, 0x808 },
		{ 0x20004, 0xff, 0x7e },
		{ 0x20040, 0x3fff, 0x1400 },
		{ 0x20344, 0x7fff, 0x500 },
		{ 0x20348, 0x7fff, 0x500 },
		{ 0x20060, 0x3fff, 0x1200 },
		{ 0x20064, 0x3fff, 0x400 },
		{ 0x20074, 0xffff, 0x1000 },
		{ 0x20070, 0xffff, 0x3000 },
		{ 0x2006c, 0xffff, 0x4000 },
		{ 0x2034c, 0x7fff, 0x3000 },
		{ 0x20088, 0xffff, 0x500 },
		{ 0x2c000, 0x3003, 0x3003 },
		{ 0x2c024, 0xff3f, 0x814 },
		{ 0x2c028, 0x3ffff, 0x400 },
	};
	u32 i, q, guard, in, out, word;
	int ret;

	h->stage = "tm-bmu";
	writel(0x000c0b0a, h->np + 0x38080);
	writel(0x000f0e0d, h->np + 0x38084);
	writel(0x000e0c0b, h->np + 0x38088);
	for (i = 0; i < 2; i++)
		change(h, regs[i].reg, regs[i].mask, regs[i].value);
	bmu_prepare(h);
	for (i = 2; i < ARRAY_SIZE(regs); i++)
		change(h, regs[i].reg, regs[i].mask, regs[i].value);
	writel(0x2bb82fa0, h->np + 0x24014);
	writel(0x40003000, h->np + 0x24060);
	writel((70 << 18) | (20 << 9) | 50, h->np + 0x24068);
	h->stage = "qmg-ram";
	change(h, 0x24000, 0x1f, 0x1f);
	ret = wait_bits(h, 0x24004, 0x1f, 0x1f);
	if (ret)
		return ret;
	writel(0x000a0001, h->np + 0x2024c);
	writel(0x60002000, h->np + 0x20084);
	change(h, 0x20010, 0x3fff, 272);
	for (i = 0; i < 5; i++)
		change(h, 0x20354 + 4 * i, 0x3fff, 272);
	change(h, 0x20368, 0xffff, 0x100);
	for (i = 0; i < 7; i++)
		change(h, 0x20260 + 4 * i, 0x3fff, 15);
	h->stage = "red-ram";
	for (q = 0; q < 401; q++) {
		guard = 0x20;
		in = 0x834;
		out = 0xc00;
		if (q >= 320 && q < 360) {
			guard = 0x40; in = 0x200; out = 0xe00;
		} else if (q >= 360 && q < 376) {
			guard = 0x80; in = 0x400; out = 0xe00;
		} else if (q == 400) {
			in = 0x400; out = 0x80;
		}
		word = (out << 12) | (q == 400 ? 0 : 0x20);
		ret = red_write(h, 0, q, &word, 1);
		if (ret)
			return ret;
		word = guard | (in << 14);
		ret = red_write(h, 2, q, &word, 1);
		if (ret)
			return ret;
		ret = red_write(h, 4, q, cfg, 4);
		if (ret)
			return ret;
	}
	writel(250000000, h->np + 0x2c004);
	return 0;
}

static void idm_prepare(struct skd840n_lan4_hw *h)
{
	void __iomem *d = h->np + SKD840N_IDM_OFFSET;
	u32 v, i;

	/* Native 1024-entry depth code 0; preserve unrelated control fields. */
	v = readl(d);
	v = ((v | 0xf0000) & 0xf00fffff) | 0x01f00000 | 0x7000;
	v &= 0x8fffffff;
	writel(v, d);
	writel(lower_32_bits(h->tx_dma), d + 4);
	writel(lower_32_bits(h->rx.descriptors), d + 8);
	writel(readl(d + 0xc) & 0xfff8ffff, d + 0xc);
	writel(64, d + 0x10);
	for (i = 0x14; i <= 0x34; i += 4)
		writel(0x00010001, d + i);
	writel(4096, d + 0x38);
	writel(SKD840N_IDM_ALL_MASK, d + 0x40);
	writel(SKD840N_IDM_RX_MASK, d + 0x44);
	writel(0, d + 0x48);
	writel(0, d + 0x4c);
	writel(0, d + 0x50);
	writel(0x06060606, d + 0x54);
	writel(0x00060606, d + 0x58);
	writel(0x07070707, d + 0x5c);
	writel(0x07070707, d + 0x60);
	/* Limit the copied normal frame to 2048 bytes, within our 2304-byte buffers.
	 * Shared IDM capacity setting follows the 2048-byte direct-mode frame limit.
	 */
	writel(0x801, d + 0x6c);
	writel(h->rx.depth - 24, d + 0x70);
	writel(0x210, d + 0x74);
	writel(20, d + 0x90);
	writel(1, d + 0x94);
	writel(0, d + 0xbc);
	writel(0, d + 0xc0);
	writel(lower_32_bits(h->rx.normal_bp), d + 0x104);
	writel(lower_32_bits(h->rx.jumbo_bp), d + 0x108);
	writel(0x00203d00, d + 0x10c); /* RX8192, return65536, enable bit8. */
	writel(0x00003d00, d + 0x110); /* Jumbo4096, return65536. */
	writel(lower_32_bits(h->free_dma), d + 0x118);
	writel(lower_32_bits(h->free_dma + 0x40000), d + 0x11c);
	writel(128, d + 0x124);
	change(h, SKD840N_IDM_OFFSET + 0x2c8, 0x3f000000, 0x12000000);
	writel(0xf49, d + 0x3fc);
	writel(lower_32_bits(h->free_dma + 0x80000), d + 0x408);
	writel(0x3d00, d + 0x40c);
	writel(0, d + 0x490);
	writel(7, d + 0x5c0);
}

int skd840n_lan4_prepare(struct skd840n_lan4_hw *h)
{
	static const u32 inport[8] = {
		0x1c001c00, 0x1c001c00, 0x1c001c00, 0x1c001c00,
		0x1c001d00, 0x1d001c00, 0x1fff1fff, 0x1c001fff,
	};
	u32 i;
	int ret = reset_path(h);

	if (ret)
		return ret;
	ret = tm_prepare(h);
	if (ret)
		return ret;
	h->stage = "smct-isu";
	writel(0x1f00, h->np + 0xc000);
	writel(0x1fff, h->np + 0xc004);
	writel(9, h->np + 0xc008);
	for (i = 0; i < 4; i++)
		change(h, 0xc028 + 4 * i, 0x3fff3fff,
		       i == 3 ? 0x1d001fff : 0x1b001b00);
	for (i = 0; i < 8; i++)
		change(h, 0xc03c + 4 * i, 0x3fff3fff, inport[i]);
	change(h, 0xc038, 0x3fff, 0x1c00);
	writel(0x1a00, h->np + 0xc05c);
	change(h, 0xc060, 0x3fffffff, 0x1a001a00);
	ret = wait_bits(h, 0x10004, 7, 0);
	if (ret)
		return ret;
	change(h, 0x10004, 7, 7);
	ret = wait_bits(h, 0x10004, 7, 0);
	if (ret)
		return ret;
	writel(0x01010202, h->np + 0x10040);
	change(h, 0x1010c, 0xfff, 3);
	change(h, 0x2040, 0x0fffffff, (2048 << 14) | 12);
	change(h, 0x2044, 0x3fffffff, (2048 << 16) | 12);
	change(h, 0x4000, 7, 2); /* Keep ingress disabled. */
	writel(0x1042, h->np + 0x4010);
	h->stage = "idm-own-rings";
	idm_prepare(h);
	h->stage = "smac3-internal";
	change(h, 0x20, 0x03800000, 0); /* Stock UNI mode 0 for internal GE. */
	writel(0x00ba2200, h->np + SKD840N_SMAC3_OFFSET);
	change(h, 0x2c0004, BIT(3), 0);
	usleep_range(400, 500);
	change(h, 0x2c0004, BIT(3), BIT(3));
	writel(0x00fa2200, h->np + SKD840N_SMAC3_OFFSET);
	writel(2048, h->np + SKD840N_SMAC3_OFFSET + 4);
	writel(0x80000001, h->np + SKD840N_SMAC3_OFFSET + 8);
	change(h, SKD840N_SMAC3_OFFSET + 0xb00, BIT(9), BIT(9));
	writel(0x11200, h->np + SKD840N_SMAC3_OFFSET + 0xe0);
	change(h, 0x343f0, BIT(3), 0);
	ret = wait_bits(h, 0x342a0, 1, 1);
	if (ret)
		return ret;
	writel(1, h->np + 0x342bc);
	/* Direct CPU path: do not load or patch PPS/PPU firmware. */
	change(h, 0x10000, 0xe, 6); /* SPA + debug; OUT still disabled. */
	change(h, 0x14000, 5, 5);
	change(h, SKD840N_IDM_OFFSET, BIT(14), BIT(14));
	h->stage = "prepared";
	return 0;
}

int skd840n_lan4_enable(struct skd840n_lan4_hw *h)
{
	/* Called only after initial RX credits and NAPI have been published. */
	change(h, 0x10000, BIT(3), BIT(3));
	change(h, 0x4000, 7, 3);
	h->stage = "direct-ready";
	return 0;
}
