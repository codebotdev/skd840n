// SPDX-License-Identifier: GPL-2.0-only
/*
 * SK-D840N SPI-NAND inspection controller. No array writes are implemented.
 * Register/PIO/IRQ sequence adapted from cnjn's spi-zx279133-sfc.c,
 * commit 07f8687248578d4be6931c665ff5d08bb6cc3d9d.
 * See docs/SOURCES-io.sha256 and docs/IO-BRINGUP.md.
 */

#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/completion.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/regmap.h>
#include <linux/reset.h>
#include <linux/spi/spi.h>
#include <linux/spi/spi-mem.h>
#include <linux/string.h>

#define ZX_SFC_CTRL		0x04
#define ZX_SFC_TRIGGER		0x08
#define ZX_SFC_CFG		0x0c
#define ZX_SFC_MODE		0x10
#define ZX_SFC_FORMAT		0x14
#define ZX_SFC_LENGTH		0x18
#define ZX_SFC_ADDRESS		0x1c
#define ZX_SFC_COMMAND		0x20
#define ZX_SFC_IRQ_ENABLE	0x28
#define ZX_SFC_STATUS		0x2c
#define ZX_SFC_IRQ_STATUS	0x30
#define ZX_SFC_FIFO_STATUS	0x34
#define ZX_SFC_FIFO		0x38
#define ZX_SFC_START		BIT(0)
#define ZX_SFC_DATA_OUT		BIT(0)
#define ZX_SFC_DATA_IN		BIT(1)
#define ZX_SFC_DUMMY		BIT(2)
#define ZX_SFC_ADDR		BIT(4)
#define ZX_SFC_ERROR		0x36
#define ZX_SFC_RX_COUNT		GENMASK(12, 8)
#define ZX_SFC_TX_SPACE		GENMASK(20, 16)
#define ZX_SFC_IRQ_ALL		0x37
#define ZX_SFC_CFG_DEFAULT	0x0001c440
#define ZX_SFC_TIMEOUT_US	100000
#define ZX_SFC_MAX_IO		4096
#define SKD840N_PAGE_WITH_OOB	2176

struct skd840n_sfc {
	struct device *dev;
	void __iomem *base;
	struct reset_control *reset;
	struct regmap *cs_map;
	struct completion done;
	struct mutex lock;
	int irq;
	u32 irq_status;
	bool known_nand;
};

/*
 * A fail-closed command gate, independent of DT partition flags. In
 * particular 06 (WREN), 02/32/84/34 (program load), 10 (program execute),
 * d8 (erase) and a1 (bad-block LUT write) can NEVER reach the controller.
 *
 * SPI-NAND selects a write-cache template even for a read-only MTD, so
 * supports_op() must describe transport capability, not this policy.
 * Returning -EROFS here also prevents spi-mem's -EOPNOTSUPP fallback.
 */
static int skd840n_sfc_readonly_op(const struct spi_mem_op *op, bool known)
{
	u8 value;

	if (op->cmd.opcode == 0xff)
		return !op->addr.nbytes && !op->dummy.nbytes &&
		       op->data.dir == SPI_MEM_NO_DATA && !op->data.nbytes ?
		       0 : -EROFS;
	if (op->cmd.opcode == 0x9f)
		return op->addr.nbytes <= 1 && !op->addr.val &&
		       op->dummy.nbytes <= 1 && op->data.dir == SPI_MEM_DATA_IN &&
		       op->data.nbytes >= 3 && op->data.nbytes <= 8 ? 0 : -EROFS;
	if (op->cmd.opcode == 0x0f)
		return op->addr.nbytes == 1 && op->addr.val <= 0xff &&
		       !op->dummy.nbytes && op->data.dir == SPI_MEM_DATA_IN &&
		       op->data.nbytes == 1 ? 0 : -EROFS;

	/* Feature semantics below are ONLY for the observed ef aa 22 NAND. */
	if (!known)
		return -ENODEV;

	switch (op->cmd.opcode) {
	case 0x1f:
		if (op->addr.nbytes != 1 || op->dummy.nbytes ||
		    op->data.dir != SPI_MEM_DATA_OUT || op->data.nbytes != 1)
			return -EROFS;
		value = *(const u8 *)op->data.buf.out;
		/* Volatile block unlock requested by SPI-NAND initialization. */
		if (op->addr.val == 0xa0 && !value)
			return 0;
		/* Only ECC and buffered-read bits; no OTP enable/lock bits. */
		if (op->addr.val == 0xb0 && !(value & ~0x18))
			return 0;
		return -EROFS;
	case 0x13:
		return op->addr.nbytes == 3 && op->addr.val < 131072 &&
		       !op->dummy.nbytes && op->data.dir == SPI_MEM_NO_DATA &&
		       !op->data.nbytes ?
		       0 : -EROFS;
	case 0x03:
	case 0x0b:
		return op->addr.nbytes == 2 && op->dummy.nbytes <= 1 &&
		       op->data.dir == SPI_MEM_DATA_IN && op->data.nbytes &&
		       op->addr.val < SKD840N_PAGE_WITH_OOB &&
		       op->data.nbytes <= SKD840N_PAGE_WITH_OOB - op->addr.val ?
		       0 : -EROFS;
	default:
		return -EROFS;
	}
}

static int zx_sfc_wait_idle(struct skd840n_sfc *sfc)
{
	u32 value;

	return readl_poll_timeout(sfc->base + ZX_SFC_CTRL, value,
				 !(value & ZX_SFC_START), 1, ZX_SFC_TIMEOUT_US);
}

static void zx_sfc_collect_irq(struct skd840n_sfc *sfc)
{
	u32 status = readl(sfc->base + ZX_SFC_IRQ_STATUS);

	sfc->irq_status |= status;
	writel(status, sfc->base + ZX_SFC_IRQ_STATUS);
}

static int zx_sfc_stop(struct skd840n_sfc *sfc)
{
	int ret;

	writel(0, sfc->base + ZX_SFC_IRQ_ENABLE);
	synchronize_irq(sfc->irq);
	zx_sfc_collect_irq(sfc);
	writel(0, sfc->base + ZX_SFC_TRIGGER);
	ret = zx_sfc_wait_idle(sfc);
	zx_sfc_collect_irq(sfc);
	if (ret) {
		/* Preserve the error even if resetting the controller recovers. */
		if (reset_control_reset(sfc->reset)) {
			dev_err_ratelimited(sfc->dev, "controller recovery failed\n");
		} else {
			writel(0, sfc->base + ZX_SFC_IRQ_ENABLE);
			writel(0, sfc->base + ZX_SFC_TRIGGER);
			zx_sfc_collect_irq(sfc);
			if (zx_sfc_wait_idle(sfc))
				dev_err_ratelimited(sfc->dev, "controller still busy\n");
		}
	}
	return ret;
}

static int zx_sfc_wait_fifo(struct skd840n_sfc *sfc, u32 mask)
{
	u32 value;
	int ret;

	ret = readl_poll_timeout(sfc->base + ZX_SFC_FIFO_STATUS, value,
		(value & mask) || (readl(sfc->base + ZX_SFC_STATUS) & ZX_SFC_ERROR),
		1, ZX_SFC_TIMEOUT_US);
	if (ret)
		return ret;
	return readl(sfc->base + ZX_SFC_STATUS) & ZX_SFC_ERROR ? -EIO : 0;
}

static int zx_sfc_pio(struct skd840n_sfc *sfc, const struct spi_mem_op *op)
{
	bool input = op->data.dir == SPI_MEM_DATA_IN;
	unsigned int len = op->data.nbytes;
	const u8 *src = op->data.buf.out;
	u8 *dst = op->data.buf.in;
	u32 mask = input ? ZX_SFC_RX_COUNT : ZX_SFC_TX_SPACE;

	while (len) {
		u32 words, status;
		int ret = zx_sfc_wait_fifo(sfc, mask);

		if (ret)
			return ret;
		status = readl(sfc->base + ZX_SFC_FIFO_STATUS);
		words = input ? FIELD_GET(ZX_SFC_RX_COUNT, status) :
				FIELD_GET(ZX_SFC_TX_SPACE, status);
		while (words-- && len) {
			unsigned int count = min_t(unsigned int, len, 4);
			__le32 value = 0;

			if (input) {
				value = cpu_to_le32(readl(sfc->base + ZX_SFC_FIFO));
				memcpy(dst, &value, count);
				dst += count;
			} else {
				memcpy(&value, src, count);
				writel(le32_to_cpu(value), sfc->base + ZX_SFC_FIFO);
				src += count;
			}
			len -= count;
		}
	}
	return 0;
}

static bool zx_sfc_supports_op(struct spi_mem *mem, const struct spi_mem_op *op)
{
	if (!spi_mem_default_supports_op(mem, op))
		return false;
	/* First bring-up uses only the stock board's 1-1-1 transfers. */
	return op->cmd.nbytes == 1 && op->cmd.buswidth == 1 && !op->cmd.dtr &&
	       op->addr.nbytes <= 4 && !op->addr.dtr &&
	       (!op->addr.nbytes || op->addr.buswidth == 1) &&
	       op->dummy.nbytes <= 1 && !op->dummy.dtr &&
	       (!op->dummy.nbytes || op->dummy.buswidth == 1) &&
	       !op->data.dtr && (!op->data.nbytes || op->data.buswidth == 1);
}

static int zx_sfc_adjust_op_size(struct spi_mem *mem, struct spi_mem_op *op)
{
	op->data.nbytes = min_t(unsigned int, op->data.nbytes, ZX_SFC_MAX_IO);
	return 0;
}

static int zx_sfc_exec_op(struct spi_mem *mem, const struct spi_mem_op *op)
{
	struct skd840n_sfc *sfc = spi_controller_get_devdata(mem->spi->controller);
	u32 format = 0, mode = 0;
	int ret, stop_ret;

	if (!zx_sfc_supports_op(mem, op) || spi_get_chipselect(mem->spi, 0))
		return -EOPNOTSUPP;
	if (op->data.nbytes && !op->data.buf.in)
		return -EINVAL;

	mutex_lock(&sfc->lock);
	ret = skd840n_sfc_readonly_op(op, sfc->known_nand);
	if (ret)
		goto out_unlock;

	ret = zx_sfc_wait_idle(sfc);
	if (ret) {
		zx_sfc_stop(sfc);
		goto out_unlock;
	}
	/* Stock DT: LSP0 0x2c bit 29 selects flash CS; CS0 only. */
	ret = regmap_update_bits(sfc->cs_map, 0x2c, BIT(29), 0);
	if (ret)
		goto out_unlock;

	if (op->addr.nbytes) {
		format |= (op->addr.nbytes - 1) << 5;
		mode |= ZX_SFC_ADDR;
	}
	if (op->dummy.nbytes) {
		format |= op->dummy.nbytes << 12;
		mode |= ZX_SFC_DUMMY;
	}
	if (op->data.dir == SPI_MEM_DATA_IN)
		mode |= ZX_SFC_DATA_IN;
	else if (op->data.dir == SPI_MEM_DATA_OUT)
		mode |= ZX_SFC_DATA_OUT;
	/* RESET preserves the chip identity, including during MTD resume. */

	reinit_completion(&sfc->done);
	sfc->irq_status = 0;
	writel(1, sfc->base + ZX_SFC_TRIGGER);
	writel(op->cmd.opcode, sfc->base + ZX_SFC_COMMAND);
	writel(ZX_SFC_CFG_DEFAULT, sfc->base + ZX_SFC_CFG);
	writel(mode, sfc->base + ZX_SFC_MODE);
	writel(format, sfc->base + ZX_SFC_FORMAT);
	writel(op->addr.val, sfc->base + ZX_SFC_ADDRESS);
	writel(op->data.nbytes ? op->data.nbytes - 1 : 0,
	       sfc->base + ZX_SFC_LENGTH);
	writel(0xff, sfc->base + ZX_SFC_IRQ_STATUS);
	writel(ZX_SFC_IRQ_ALL, sfc->base + ZX_SFC_IRQ_ENABLE);
	writel(ZX_SFC_START, sfc->base + ZX_SFC_CTRL);

	ret = op->data.nbytes ? zx_sfc_pio(sfc, op) : 0;
	if (!ret && !wait_for_completion_timeout(&sfc->done, msecs_to_jiffies(1000)))
		ret = -ETIMEDOUT;
	stop_ret = zx_sfc_stop(sfc);
	if (stop_ret)
		ret = stop_ret;
	else if (!ret && ((sfc->irq_status |
			  readl(sfc->base + ZX_SFC_STATUS)) & ZX_SFC_ERROR))
		ret = -EIO;

	if (!ret && op->cmd.opcode == 0x9f) {
		const u8 *id = op->data.buf.in;

		sfc->known_nand = id[0] == 0xef && id[1] == 0xaa && id[2] == 0x22;
	}

out_unlock:
	mutex_unlock(&sfc->lock);
	return ret;
}

static const struct spi_controller_mem_ops zx_sfc_mem_ops = {
	.supports_op = zx_sfc_supports_op,
	.adjust_op_size = zx_sfc_adjust_op_size,
	.exec_op = zx_sfc_exec_op,
};

static irqreturn_t zx_sfc_irq(int irq, void *data)
{
	struct skd840n_sfc *sfc = data;
	u32 status = readl(sfc->base + ZX_SFC_IRQ_STATUS);

	if (!status)
		return IRQ_NONE;
	sfc->irq_status |= status;
	writel(status, sfc->base + ZX_SFC_IRQ_STATUS);
	complete(&sfc->done);
	return IRQ_HANDLED;
}

static int zx_sfc_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct spi_controller *ctlr;
	struct skd840n_sfc *sfc;
	struct clk *clk;
	unsigned long rate;
	u32 max_freq;
	int ret;

	ctlr = devm_spi_alloc_host(dev, sizeof(*sfc));
	if (!ctlr)
		return -ENOMEM;
	sfc = spi_controller_get_devdata(ctlr);
	sfc->dev = dev;
	mutex_init(&sfc->lock);
	init_completion(&sfc->done);
	sfc->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(sfc->base))
		return PTR_ERR(sfc->base);
	sfc->cs_map = syscon_regmap_lookup_by_phandle(dev->of_node, "skyworth,lsp0");
	if (IS_ERR(sfc->cs_map))
		return dev_err_probe(dev, PTR_ERR(sfc->cs_map), "missing LSP0 syscon\n");

	clk = devm_clk_get_enabled(dev, "pclk");
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "enabling pclk failed\n");
	clk = devm_clk_get_enabled(dev, "wclk");
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "enabling wclk failed\n");
	sfc->reset = devm_reset_control_get_exclusive(dev, NULL);
	if (IS_ERR(sfc->reset))
		return dev_err_probe(dev, PTR_ERR(sfc->reset), "getting reset failed\n");
	ret = reset_control_reset(sfc->reset);
	if (ret)
		return dev_err_probe(dev, ret, "controller reset failed\n");

	ret = device_property_read_u32(dev, "spi-max-frequency", &max_freq);
	if (ret || !max_freq || max_freq > 25000000)
		return dev_err_probe(dev, -EINVAL, "expected 1..25000000 Hz\n");
	ret = clk_set_rate(clk, max_freq);
	if (ret)
		return dev_err_probe(dev, ret, "setting wclk failed\n");
	rate = clk_get_rate(clk);
	if (!rate || rate > max_freq)
		return dev_err_probe(dev, -ERANGE, "invalid wclk rate %lu\n", rate);

	writel(0, sfc->base + ZX_SFC_IRQ_ENABLE);
	writel(0xff, sfc->base + ZX_SFC_IRQ_STATUS);
	sfc->irq = platform_get_irq(pdev, 0);
	if (sfc->irq < 0)
		return sfc->irq;
	ret = devm_request_irq(dev, sfc->irq, zx_sfc_irq, 0, dev_name(dev), sfc);
	if (ret)
		return dev_err_probe(dev, ret, "requesting IRQ failed\n");

	ctlr->mem_ops = &zx_sfc_mem_ops;
	ctlr->num_chipselect = 1;
	ctlr->max_speed_hz = rate;
	ctlr->bits_per_word_mask = SPI_BPW_MASK(8);
	ctlr->dev.of_node = dev->of_node;
	/* Deliberately no generic SPI transfer or spidev escape hatch. */
	ret = devm_spi_register_controller(dev, ctlr);
	if (ret)
		return dev_err_probe(dev, ret, "registering controller failed\n");
	dev_info(dev, "array-read-only SPI-NAND inspection at %lu Hz (PIO)\n", rate);
	return 0;
}

static const struct of_device_id zx_sfc_match[] = {
	{ .compatible = "skyworth,sk-d840n-sfc-ro" },
	{ }
};
MODULE_DEVICE_TABLE(of, zx_sfc_match);

static struct platform_driver zx_sfc_driver = {
	.probe = zx_sfc_probe,
	.driver = {
		.name = "skd840n-sfc-ro",
		.of_match_table = zx_sfc_match,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(zx_sfc_driver);

MODULE_DESCRIPTION("SK-D840N fail-closed read-only SPI-NAND inspection");
MODULE_LICENSE("GPL");
