// SPDX-License-Identifier: GPL-2.0-only
/*
 * SK-D840N PHY identification, not an Ethernet or PHY driver.
 * MDIO register protocol derived from cnjn's mdio-zx279133.c at
 * 07f8687248578d4be6931c665ff5d08bb6cc3d9d. See docs/SOURCES-io.sha256.
 *
 * No PHY writes, PHY resets, automatic enumeration or probe-time transactions.
 * ID reads cover the stock DT candidates; link reads require witnessed IDs.
 * Reading status registers consumes latched-low link indications.
 * Clause 45 address cycles select a register; they do not write its value.
 */

#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/device.h>
#include <linux/errno.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/kernel.h>
#include <linux/limits.h>
#include <linux/mdio.h>
#include <linux/mii.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/reset.h>
#include <linux/slab.h>
#include <linux/sysfs.h>

#define ZX_MDIO_READ_DATA	0x08
#define ZX_MDIO_C45_REG		0x0c
#define ZX_MDIO_STATUS		0x10
#define ZX_MDIO_CONTROL		0x14
#define ZX_MDIO_DONE		BIT(0)
#define ZX_MDIO_REG		GENMASK(4, 0)
#define ZX_MDIO_PHY		GENMASK(9, 5)
#define ZX_MDIO_OP		GENMASK(11, 10)
#define ZX_MDIO_C22		BIT(12)
#define ZX_MDIO_C45		BIT(13)
#define ZX_MDIO_START		BIT(14)
#define ZX_MDIO_C22_READ		2
#define ZX_MDIO_C45_READ		3
#define ZX_MDIO_TIMEOUT_US	10000

struct skd840n_mdio {
	void __iomem *base;
	struct mutex lock;
};

/* Candidates, not confirmed PHY addresses or physical port assignments. */
static const u8 skd840n_phy_candidates[] = { 2, 5, 10, 11, 12, 13, 28 };

static int skd840n_mdio_transfer(struct skd840n_mdio *mdio, u32 control)
{
	u32 status;
	int ret;

	writel(0, mdio->base + ZX_MDIO_STATUS);
	writel(control | ZX_MDIO_START, mdio->base + ZX_MDIO_CONTROL);
	ret = readl_poll_timeout(mdio->base + ZX_MDIO_STATUS, status,
				 status & ZX_MDIO_DONE, 1, ZX_MDIO_TIMEOUT_US);
	writel(0, mdio->base + ZX_MDIO_STATUS);
	writel(control, mdio->base + ZX_MDIO_CONTROL);
	return ret;
}

/* Caller holds lock across both halves of a Clause 45 transaction. */
static int skd840n_mdio_read_reg(struct skd840n_mdio *mdio, u8 phy,
			       int mmd, u8 reg)
{
	u32 control = FIELD_PREP(ZX_MDIO_PHY, phy);
	int ret;

	/* No vendor/page registers or C45-over-C22 data writes. */
	if (phy > 31 || reg > MII_PHYSID2 ||
	    (mmd != -1 && mmd != MDIO_MMD_PMAPMD && mmd != MDIO_MMD_PCS))
		return -EINVAL;

	if (mmd < 0) {
		control |= ZX_MDIO_C22 | FIELD_PREP(ZX_MDIO_REG, reg) |
			   FIELD_PREP(ZX_MDIO_OP, ZX_MDIO_C22_READ);
	} else {
		control |= ZX_MDIO_C45 | FIELD_PREP(ZX_MDIO_REG, mmd);
		writel(reg, mdio->base + ZX_MDIO_C45_REG);
		/* OP=0 is the C45 address phase, not a data write. */
		ret = skd840n_mdio_transfer(mdio, control);
		if (ret)
			return ret;
		control |= FIELD_PREP(ZX_MDIO_OP, ZX_MDIO_C45_READ);
	}

	ret = skd840n_mdio_transfer(mdio, control);
	if (ret)
		return ret;
	return readl(mdio->base + ZX_MDIO_READ_DATA) & 0xffff;
}

static ssize_t skd840n_mdio_ids(struct device *dev, char *buf, bool c45)
{
	struct skd840n_mdio *mdio = dev_get_drvdata(dev);
	ssize_t len = 0;
	unsigned int i, j;
	int hi, lo, mmd;
	u32 id;

	mutex_lock(&mdio->lock);
	len += sysfs_emit_at(buf, len, "candidate clause mmd phy_id result\n");
	for (i = 0; i < ARRAY_SIZE(skd840n_phy_candidates); i++) {
		for (j = 0; j < (c45 ? 2 : 1); j++) {
			/* Standard PMA/PMD and PCS IDs, never vendor registers. */
			mmd = c45 ? (j ? 3 : 1) : -1;
			hi = skd840n_mdio_read_reg(mdio, skd840n_phy_candidates[i],
						 mmd, 2);
			lo = hi < 0 ? hi :
				skd840n_mdio_read_reg(mdio,
					skd840n_phy_candidates[i], mmd, 3);
			if (hi < 0 || lo < 0) {
				len += sysfs_emit_at(buf, len,
					"%02x %u %d -------- error=%d\n",
					skd840n_phy_candidates[i], c45 ? 45 : 22,
					mmd, hi < 0 ? hi : lo);
				continue;
			}
			id = (u32)hi << 16 | lo;
			len += sysfs_emit_at(buf, len, "%02x %u %d %08x %s\n",
				skd840n_phy_candidates[i], c45 ? 45 : 22, mmd, id,
				!id || id == U32_MAX ? "no-id" : "candidate-id");
		}
	}
	mutex_unlock(&mdio->lock);
	return len;
}

static ssize_t phy_ids_show(struct device *dev,
			    struct device_attribute *attr, char *buf)
{
	return skd840n_mdio_ids(dev, buf, false);
}
static DEVICE_ATTR_RO(phy_ids);

static ssize_t phy_ids_c45_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	return skd840n_mdio_ids(dev, buf, true);
}
static DEVICE_ATTR_RO(phy_ids_c45);

/* IDs witnessed on this board; this is not a physical-port assignment. */
static bool skd840n_mdio_link_id(u32 id, int mmd)
{
	if (mmd == -1)
		return id == 0x84b95032 || id == 0x001cc849;
	return (mmd == MDIO_MMD_PMAPMD || mmd == MDIO_MMD_PCS) &&
	       id == 0x001cc849;
}

static int skd840n_mdio_get_id(struct skd840n_mdio *mdio, u8 phy,
			     int mmd, u32 *id)
{
	int hi, lo;

	hi = skd840n_mdio_read_reg(mdio, phy, mmd, MII_PHYSID1);
	if (hi < 0)
		return hi;
	lo = skd840n_mdio_read_reg(mdio, phy, mmd, MII_PHYSID2);
	if (lo < 0)
		return lo;
	*id = (u32)hi << 16 | lo;
	return 0;
}

/* Caller holds the bus lock. Preserve both latch and current observations. */
static int skd840n_mdio_sample_link(struct skd840n_mdio *mdio, u8 phy,
				    int mmd, u32 id, int *ctrl,
				    int *first, int *now)
{
	u32 after;
	int ret;

	if (!skd840n_mdio_link_id(id, mmd))
		return -EOPNOTSUPP;

	*ctrl = skd840n_mdio_read_reg(mdio, phy, mmd, MII_BMCR);
	if (*ctrl < 0)
		return *ctrl;
	*first = skd840n_mdio_read_reg(mdio, phy, mmd, MII_BMSR);
	if (*first < 0)
		return *first;
	*now = skd840n_mdio_read_reg(mdio, phy, mmd, MII_BMSR);
	if (*now < 0)
		return *now;

	/* An all-ones bus response must never be reported as link-up. */
	if (*ctrl == 0xffff || *first == 0xffff || *now == 0xffff)
		return -ENODATA;
	ret = skd840n_mdio_get_id(mdio, phy, mmd, &after);
	if (ret)
		return ret;
	if (after != id)
		return -ESTALE;

	/* Both C22 BMSR and C45 STAT1 use bit 2. No speed inference. */
	return !!(*now & BMSR_LSTATUS);
}

static ssize_t skd840n_mdio_links(struct device *dev, char *buf, bool c45)
{
	struct skd840n_mdio *mdio = dev_get_drvdata(dev);
	ssize_t len = 0;
	unsigned int i, j;
	int ret, mmd, ctrl, first, now;
	u32 id;
	u8 phy;

	mutex_lock(&mdio->lock);
	len += sysfs_emit_at(buf, len,
		"candidate clause mmd phy_id ctrl stat_first stat_now link result\n");
	for (i = 0; i < ARRAY_SIZE(skd840n_phy_candidates); i++) {
		phy = skd840n_phy_candidates[i];
		for (j = 0; j < (c45 ? 2 : 1); j++) {
			mmd = c45 ? (j ? MDIO_MMD_PCS : MDIO_MMD_PMAPMD) : -1;
			ret = skd840n_mdio_get_id(mdio, phy, mmd, &id);
			if (ret) {
				len += sysfs_emit_at(buf, len,
					"%02x %u %d -------- ---- ---- ---- unknown error=%d\n",
					phy, c45 ? 45 : 22, mmd, ret);
				continue;
			}
			if (!id || id == U32_MAX || !skd840n_mdio_link_id(id, mmd)) {
				len += sysfs_emit_at(buf, len,
					"%02x %u %d %08x ---- ---- ---- unknown %s\n",
					phy, c45 ? 45 : 22, mmd, id,
					!id || id == U32_MAX ? "no-id" : "unsupported-id");
				continue;
			}
			ctrl = first = now = -1;
			ret = skd840n_mdio_sample_link(mdio, phy, mmd, id,
						      &ctrl, &first, &now);
			if (ret < 0) {
				len += sysfs_emit_at(buf, len,
					"%02x %u %d %08x ---- ---- ---- unknown error=%d\n",
					phy, c45 ? 45 : 22, mmd, id, ret);
				continue;
			}
			len += sysfs_emit_at(buf, len,
				"%02x %u %d %08x %04x %04x %04x %s ok\n",
				phy, c45 ? 45 : 22, mmd, id, ctrl, first, now,
				ret ? "up" : "down");
		}
	}
	mutex_unlock(&mdio->lock);
	return len;
}

static ssize_t phy_links_show(struct device *dev,
			      struct device_attribute *attr, char *buf)
{
	return skd840n_mdio_links(dev, buf, false);
}
static DEVICE_ATTR_RO(phy_links);

static ssize_t phy_links_c45_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	return skd840n_mdio_links(dev, buf, true);
}
static DEVICE_ATTR_RO(phy_links_c45);

static struct attribute *skd840n_mdio_attrs[] = {
	&dev_attr_phy_ids.attr,
	&dev_attr_phy_ids_c45.attr,
	&dev_attr_phy_links.attr,
	&dev_attr_phy_links_c45.attr,
	NULL,
};
ATTRIBUTE_GROUPS(skd840n_mdio);

static int skd840n_mdio_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct skd840n_mdio *mdio;
	struct reset_control *reset;
	struct clk *clk;
	int ret;

	mdio = devm_kzalloc(dev, sizeof(*mdio), GFP_KERNEL);
	if (!mdio)
		return -ENOMEM;
	mdio->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(mdio->base))
		return PTR_ERR(mdio->base);

	clk = devm_clk_get_enabled(dev, "pclk");
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "enabling pclk failed\n");
	clk = devm_clk_get_enabled(dev, "wclk");
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "enabling wclk failed\n");
	if (!clk_get_rate(clk))
		return dev_err_probe(dev, -EINVAL, "wclk has no rate\n");

	/* Only the MDIO controller's LSP reset, never a PHY GPIO reset. */
	reset = devm_reset_control_get_exclusive(dev, NULL);
	if (IS_ERR(reset))
		return dev_err_probe(dev, PTR_ERR(reset), "getting reset failed\n");
	ret = reset_control_reset(reset);
	if (ret)
		return dev_err_probe(dev, ret, "controller reset failed\n");

	mutex_init(&mdio->lock);
	platform_set_drvdata(pdev, mdio);
	dev_info(dev, "PHY-ID diagnostics ready; no Ethernet data path\n");
	return 0;
}

static const struct of_device_id skd840n_mdio_match[] = {
	{ .compatible = "skyworth,sk-d840n-mdio-diag" },
	{ }
};
MODULE_DEVICE_TABLE(of, skd840n_mdio_match);

static struct platform_driver skd840n_mdio_driver = {
	.probe = skd840n_mdio_probe,
	.driver = {
		.name = "skd840n-mdio-diag",
		.of_match_table = skd840n_mdio_match,
		.dev_groups = skd840n_mdio_groups,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(skd840n_mdio_driver);

MODULE_DESCRIPTION("SK-D840N read-only PHY-ID diagnostics");
MODULE_LICENSE("GPL");
