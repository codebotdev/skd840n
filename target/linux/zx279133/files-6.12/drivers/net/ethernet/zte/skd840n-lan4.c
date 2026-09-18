// SPDX-License-Identifier: GPL-2.0-only
/*
 * Integrated, one-shot SK-D840N LAN4 CPU-direct RAM test.
 * Probe registers a DOWN netdev. Only ndo_open starts the experiment.
 * Exposed DMA memory is deliberately retained until a COLD POWER CYCLE:
 * stopping software/IRQs is not proof that all hardware DMA has stopped.
 * Built-in only; no unbind, remove, suspend, restart or hotplug contract.
 * See docs/LAN4-TEST.md for the evidence and limited test environment.
 */
#include <linux/atomic.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/etherdevice.h>
#include <linux/interrupt.h>
#include <linux/iopoll.h>
#include <linux/jiffies.h>
#include <linux/mii.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/netdevice.h>
#include <linux/of.h>
#include <linux/of_reserved_mem.h>
#include <linux/platform_device.h>
#include <linux/reset.h>
#include <linux/rtnetlink.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/sysfs.h>
#include <linux/workqueue.h>
#include "skd840n-lan4.h"

#define LAN4_CLOCKS 8
#define LAN4_IRQS 4
/* Below TX depth: do not reuse a TX payload slot during this experiment. */
#define LAN4_PACKET_LIMIT 512U
#define LAN4_RETRY_LIMIT 100U
#define LAN4_TICK_MS 20U

struct lan4_priv {
	struct skd840n_lan4_hw hw;
	struct net_device *ndev;
	struct skd840n_idm_rx *rx;
	void __iomem *mdio;
	struct clk_bulk_data clocks[LAN4_CLOCKS];
	struct reset_control *mdio_reset;
	struct napi_struct napi;
	struct delayed_work tick;
	struct mutex control;
	struct mutex mdio_lock;
	spinlock_t tx_lock;
	spinlock_t irq_lock;
	__le32 *tx_desc;
	u8 *tx_data;
	void *free_rings;
	dma_addr_t tx_data_dma;
	u16 lengths[SKD840N_DIRECT_TX_DEPTH];
	u16 producer, consumer, pending, done;
	u32 submitted, completed, rx_retries;
	u32 last_rx_flags, last_rx_len, last_rx_queue;
	atomic64_t rx_transport_drop, rx_busy, rx_not_ready;
	unsigned long tx_progress, link_due;
	int irqs[LAN4_IRQS], requested;
	int error, link_error;
	bool pool, clocks_on, attempted, retained, running, napi_on;
	atomic64_t irq_count, rx_packets, rx_bytes, rx_dropped;
	atomic64_t rx_consumed, tx_packets, tx_bytes, tx_dropped;
};

static const char * const lan4_clocks[LAN4_CLOCKS] = {
	"idm-aclk", "tm-aclk", "pclk", "smac-wclk", "woe1-wclk", "mac-wclk",
	"mdio-pclk", "mdio-wclk",
};

/* Only PHY0d C22 reads. START/STATUS writes are MDIO controller commands. */
static int lan4_mdio_read(struct lan4_priv *p, u32 reg)
{
	u32 ctl, v;
	int ret;

	if (reg != MII_BMCR && reg != MII_BMSR && reg != MII_PHYSID1 &&
	    reg != MII_PHYSID2 && reg != MII_CTRL1000 && reg != MII_STAT1000)
		return -EINVAL;
	ctl = reg | (SKD840N_DIRECT_PHY << 5) | (2 << 10) | BIT(12);
	writel(0, p->mdio + 0x10);
	writel(ctl | BIT(14), p->mdio + 0x14);
	ret = readl_poll_timeout(p->mdio + 0x10, v, v & 1, 1, 10000);
	writel(0, p->mdio + 0x10);
	writel(ctl, p->mdio + 0x14);
	return ret ? ret : readl(p->mdio + 8) & 0xffff;
}

static int lan4_phy(struct lan4_priv *p, bool status)
{
	static const u8 regs[] = { MII_PHYSID1, MII_PHYSID2, MII_BMCR,
		MII_BMSR, MII_BMSR, MII_CTRL1000, MII_STAT1000 };
	int v[ARRAY_SIZE(regs)], i, ret = 0;

	mutex_lock(&p->mdio_lock);
	for (i = 0; i < (status ? ARRAY_SIZE(regs) : 2); i++) {
		v[i] = lan4_mdio_read(p, regs[i]);
		if (v[i] < 0) {
			ret = v[i];
			goto out;
		}
	}
	if (((u32)v[0] << 16 | v[1]) != SKD840N_DIRECT_PHY_ID)
		ret = -ENODEV;
	else if (status)
		ret = skd840n_direct_gigabit(v[2], v[4], v[5], v[6]);
out:
	mutex_unlock(&p->mdio_lock);
	return ret;
}

static void lan4_mask(struct lan4_priv *p, bool masked)
{
	unsigned long flags;

	spin_lock_irqsave(&p->irq_lock, flags);
	writel(!masked && READ_ONCE(p->running) &&
	       !test_bit(NAPI_STATE_SCHED, &p->napi.state) ?
	       SKD840N_IDM_ALL_MASK & ~SKD840N_IDM_RX_MASK : SKD840N_IDM_ALL_MASK,
	       p->hw.np + SKD840N_IDM_OFFSET + SKD840N_IDM_MASK);
	readl(p->hw.np + SKD840N_IDM_OFFSET + SKD840N_IDM_MASK);
	spin_unlock_irqrestore(&p->irq_lock, flags);
}

/* Serialize the explicit mask with timer/IRQ schedule attempts. */
static void lan4_schedule(struct lan4_priv *p)
{
	unsigned long flags;

	spin_lock_irqsave(&p->irq_lock, flags);
	if (READ_ONCE(p->running) && napi_schedule_prep(&p->napi)) {
		writel(SKD840N_IDM_ALL_MASK,
		       p->hw.np + SKD840N_IDM_OFFSET + SKD840N_IDM_MASK);
		readl(p->hw.np + SKD840N_IDM_OFFSET + SKD840N_IDM_MASK);
		__napi_schedule(&p->napi);
	}
	spin_unlock_irqrestore(&p->irq_lock, flags);
}

/* Safe from NAPI, TX or work context; no sleep, cancellation or free here. */
static void lan4_fault(struct lan4_priv *p, int error)
{
	spin_lock_bh(&p->tx_lock);
	if (cmpxchg(&p->error, 0, error)) {
		spin_unlock_bh(&p->tx_lock);
		return;
	}
	WRITE_ONCE(p->running, false);
	lan4_mask(p, true);
	skd840n_lan4_block(&p->hw);
	netif_stop_queue(p->ndev);
	netif_carrier_off(p->ndev);
	spin_unlock_bh(&p->tx_lock);
	netdev_err(p->ndev, "test stopped: error=%d stage=%s; DMA memory retained; cold power cycle required\n",
		   error, p->hw.stage);
}

static int lan4_receive(void *priv, const void *data, u32 len,
			 const u32 words[8], u32 queue)
{
	struct lan4_priv *p = priv;
	struct sk_buff *skb;

	WRITE_ONCE(p->last_rx_flags, words[1]);
	WRITE_ONCE(p->last_rx_len, len);
	WRITE_ONCE(p->last_rx_queue, queue);
	if (!READ_ONCE(p->running) || len > SKD840N_DIRECT_FRAME_MAX ||
	    ((words[1] >> 16) & 0x3f) != SKD840N_DIRECT_PORT) {
		atomic64_inc(&p->rx_dropped);
		return -EINVAL;
	}
	skb = napi_alloc_skb(&p->napi, len + NET_IP_ALIGN);
	if (!skb) {
		atomic64_inc(&p->rx_dropped);
		return -ENOMEM;
	}
	skb_reserve(skb, NET_IP_ALIGN);
	memcpy(skb_put(skb, len), data, len);
	skb->ip_summed = CHECKSUM_NONE;
	skb->protocol = eth_type_trans(skb, p->ndev);
	napi_gro_receive(&p->napi, skb);
	atomic64_inc(&p->rx_packets);
	atomic64_add(len, &p->rx_bytes);
	return 0;
}

static int lan4_quiesce(void *priv)
{
	struct lan4_priv *p = priv;

	/* Allocation rollback before hardware exposure is the ONLY free path. */
	return p->retained ? -EBUSY : 0;
}

static const struct skd840n_idm_rx_ops lan4_rx_ops = {
	.receive = lan4_receive,
	.quiesce = lan4_quiesce,
};

/* tx_lock serializes xmit and reclaim. Impossible completion never frees. */
static int lan4_reclaim(struct lan4_priv *p)
{
	u32 done = readl(p->hw.np + SKD840N_IDM_OFFSET + SKD840N_TX_DONE) & 0xffff;
	u32 count, i, bytes = 0;
	int ret = skd840n_direct_completed(done, p->done, p->pending, &count);

	if (ret)
		return ret;
	if (!count)
		return p->pending && time_after(jiffies, p->tx_progress + 2 * HZ) ?
			-ETIMEDOUT : 0;
	dma_rmb();
	for (i = 0; i < count; i++) {
		bytes += p->lengths[p->consumer];
		p->lengths[p->consumer] = 0;
		p->consumer = (p->consumer + 1) & (SKD840N_DIRECT_TX_DEPTH - 1);
	}
	p->done = done;
	p->pending -= count;
	p->completed += count;
	p->tx_progress = jiffies;
	atomic64_add(count, &p->tx_packets);
	atomic64_add(bytes, &p->tx_bytes);
	netdev_completed_queue(p->ndev, count, bytes);
	if (READ_ONCE(p->running) && netif_carrier_ok(p->ndev) &&
	    p->pending < SKD840N_DIRECT_TX_DEPTH - 1)
		netif_wake_queue(p->ndev);
	return 0;
}

static netdev_tx_t lan4_xmit(struct sk_buff *skb, struct net_device *ndev)
{
	struct lan4_priv *p = netdev_priv(ndev);
	u32 words[8], len = max_t(u32, skb->len, ETH_ZLEN), slot, i;
	__le32 *desc;
	u8 *data;
	int ret;

	if (unlikely(skb_is_gso(skb) || len > SKD840N_DIRECT_FRAME_MAX))
		goto drop;
	if (skb->ip_summed == CHECKSUM_PARTIAL && skb_checksum_help(skb))
		goto drop;
	spin_lock_bh(&p->tx_lock);
	if (unlikely(!READ_ONCE(p->running) || !netif_carrier_ok(ndev))) {
		spin_unlock_bh(&p->tx_lock);
		goto drop;
	}
	ret = lan4_reclaim(p);
	if (ret) {
		spin_unlock_bh(&p->tx_lock);
		lan4_fault(p, ret);
		goto drop;
	}
	if (p->submitted >= LAN4_PACKET_LIMIT) {
		spin_unlock_bh(&p->tx_lock);
		lan4_fault(p, -EDQUOT);
		goto drop;
	}
	if (p->pending >= SKD840N_DIRECT_TX_DEPTH - 1) {
		netif_stop_queue(ndev);
		spin_unlock_bh(&p->tx_lock);
		return NETDEV_TX_BUSY;
	}
	slot = p->producer;
	data = p->tx_data + slot * SKD840N_DIRECT_TX_STRIDE;
	ret = skd840n_direct_tx_words(p->tx_data_dma + slot * SKD840N_DIRECT_TX_STRIDE,
				    len, words);
	if (ret || skb_copy_bits(skb, 0, data + SKD840N_DIRECT_HEADROOM, skb->len)) {
		spin_unlock_bh(&p->tx_lock);
		goto drop;
	}
	memset(data + SKD840N_DIRECT_HEADROOM + skb->len, 0, len - skb->len);
	desc = p->tx_desc + (SKD840N_DIRECT_QUEUE * SKD840N_DIRECT_TX_DEPTH + slot) * 8;
	for (i = 0; i < 8; i++)
		WRITE_ONCE(desc[i], cpu_to_le32(words[i]));
	p->lengths[slot] = len;
	p->producer = (slot + 1) & (SKD840N_DIRECT_TX_DEPTH - 1);
	if (!p->pending)
		p->tx_progress = jiffies;
	p->pending++;
	p->submitted++;
	netdev_sent_queue(ndev, len);
	/* Coherent memory still needs publication ordering before the doorbell. */
	dma_wmb();
	writel(1U << 17, p->hw.np + SKD840N_IDM_OFFSET + SKD840N_TX_DOORBELL);
	if (p->pending >= SKD840N_DIRECT_TX_DEPTH - 1)
		netif_stop_queue(ndev);
	spin_unlock_bh(&p->tx_lock);
	dev_kfree_skb_any(skb); /* Hardware reads the private bounce copy, not skb. */
	return NETDEV_TX_OK;
drop:
	atomic64_inc(&p->tx_dropped);
	dev_kfree_skb_any(skb);
	return NETDEV_TX_OK;
}

static irqreturn_t lan4_irq(int irq, void *data)
{
	struct lan4_priv *p = data;
	void __iomem *d = p->hw.np + SKD840N_IDM_OFFSET;
	u32 pending;

	if (!READ_ONCE(p->running))
		return IRQ_NONE;
	pending = readl(d + SKD840N_IDM_STATUS) & ~readl(d + SKD840N_IDM_MASK);
	if (!(pending & SKD840N_IDM_RX_MASK))
		return IRQ_NONE;
	atomic64_inc(&p->irq_count);
	lan4_schedule(p);
	return IRQ_HANDLED;
}

static int lan4_poll(struct napi_struct *napi, int budget)
{
	struct lan4_priv *p = container_of(napi, struct lan4_priv, napi);
	struct skd840n_idm_rx_stats stats;
	bool more = false;
	int ret, work;

	if (!READ_ONCE(p->running)) {
		if (budget)
			napi_complete_done(napi, 0);
		return 0;
	}
	spin_lock(&p->tx_lock);
	ret = lan4_reclaim(p);
	spin_unlock(&p->tx_lock);
	if (ret) {
		lan4_fault(p, ret);
		if (budget)
			napi_complete_done(napi, 0);
		return 0;
	}
	if (!budget)
		return 0;
	work = skd840n_idm_rx_poll(p->rx, budget, &more);
	skd840n_idm_rx_get_stats(p->rx, &stats);
	atomic64_set(&p->rx_consumed, stats.consumed);
	atomic64_set(&p->rx_transport_drop, stats.dropped);
	atomic64_set(&p->rx_busy, stats.release_busy);
	atomic64_set(&p->rx_not_ready, stats.not_ready);
	if (work < 0 || atomic64_read(&p->rx_consumed) >= LAN4_PACKET_LIMIT) {
		lan4_fault(p, work < 0 ? work : -EDQUOT);
		napi_complete_done(napi, 0);
		return 0;
	}
	if (more && !work) {
		if (++p->rx_retries >= LAN4_RETRY_LIMIT)
			lan4_fault(p, -ETIMEDOUT);
	} else {
		p->rx_retries = 0;
	}
	if (work == budget || (more && work))
		return budget;
	if (napi_complete_done(napi, work) && !more)
		lan4_mask(p, false);
	/* tick retries more=true with IRQs masked: no immediate IRQ/busy storm. */
	return work;
}

static void lan4_tick(struct work_struct *work)
{
	struct lan4_priv *p = container_of(to_delayed_work(work), struct lan4_priv, tick);
	int link, ret;

	if (!READ_ONCE(p->running))
		return;
	spin_lock_bh(&p->tx_lock);
	ret = lan4_reclaim(p);
	spin_unlock_bh(&p->tx_lock);
	if (ret) {
		lan4_fault(p, ret);
		return;
	}
	if (time_after_eq(jiffies, p->link_due)) {
		link = lan4_phy(p, true);
		WRITE_ONCE(p->link_error, link < 0 ? link : 0);
		/* Pair gate/carrier changes with TX; a concurrent fault never reopens. */
		spin_lock_bh(&p->tx_lock);
		if (READ_ONCE(p->running) && link == 1) {
			if (!netif_carrier_ok(p->ndev)) {
				skd840n_lan4_gate(&p->hw, true);
				netif_carrier_on(p->ndev);
				netif_wake_queue(p->ndev);
				netdev_info(p->ndev, "LAN4 PHY0d: 1000/full; CPU-direct experiment\n");
			}
		} else {
			skd840n_lan4_gate(&p->hw, false);
			netif_carrier_off(p->ndev);
			netif_stop_queue(p->ndev);
		}
		spin_unlock_bh(&p->tx_lock);
		p->link_due = jiffies + msecs_to_jiffies(250);
	}
	if (READ_ONCE(p->running)) {
		lan4_schedule(p);
		schedule_delayed_work(&p->tick, msecs_to_jiffies(LAN4_TICK_MS));
	}
}

static void lan4_free_unexposed(struct lan4_priv *p)
{
	struct device *dev = p->hw.dev;

	if (WARN_ON(p->retained))
		return;
	if (p->rx)
		skd840n_idm_rx_destroy(&p->rx);
	if (p->free_rings)
		dma_free_coherent(dev, SKD840N_FREE_BYTES, p->free_rings, p->hw.free_dma);
	if (p->tx_data)
		dma_free_coherent(dev, SKD840N_TX_DATA_BYTES, p->tx_data, p->tx_data_dma);
	if (p->tx_desc)
		dma_free_coherent(dev, SKD840N_TX_DESC_BYTES, p->tx_desc, p->hw.tx_dma);
	if (p->hw.bmu)
		dma_free_coherent(dev, SKD840N_BMU_BYTES, p->hw.bmu, p->hw.bmu_dma);
	p->free_rings = NULL;
	p->tx_data = NULL;
	p->tx_desc = NULL;
	p->hw.bmu = NULL;
}

static int lan4_alloc(struct lan4_priv *p)
{
	struct device *dev = p->hw.dev;
	int ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));

	if (ret)
		return ret;
	/* Allocate the largest pool block first; no 32 MiB buddy allocation. */
	p->hw.bmu = dma_alloc_coherent(dev, SKD840N_BMU_BYTES, &p->hw.bmu_dma, GFP_KERNEL);
	p->tx_desc = dma_alloc_coherent(dev, SKD840N_TX_DESC_BYTES, &p->hw.tx_dma, GFP_KERNEL);
	p->tx_data = dma_alloc_coherent(dev, SKD840N_TX_DATA_BYTES, &p->tx_data_dma, GFP_KERNEL);
	p->free_rings = dma_alloc_coherent(dev, SKD840N_FREE_BYTES, &p->hw.free_dma, GFP_KERNEL);
	if (!p->hw.bmu || !p->tx_desc || !p->tx_data || !p->free_rings)
		return -ENOMEM;
	if (upper_32_bits(p->hw.bmu_dma + SKD840N_BMU_BYTES - 1) ||
	    upper_32_bits(p->hw.tx_dma + SKD840N_TX_DESC_BYTES - 1) ||
	    upper_32_bits(p->tx_data_dma + SKD840N_TX_DATA_BYTES - 1) ||
	    upper_32_bits(p->hw.free_dma + SKD840N_FREE_BYTES - 1))
		return -ERANGE;
	memset(p->hw.bmu, 0, SKD840N_BMU_BYTES);
	memset(p->tx_desc, 0, SKD840N_TX_DESC_BYTES);
	memset(p->tx_data, 0, SKD840N_TX_DATA_BYTES);
	memset(p->free_rings, 0, SKD840N_FREE_BYTES);
	p->rx = skd840n_idm_rx_alloc(dev, p->hw.np + SKD840N_IDM_OFFSET, &lan4_rx_ops, p);
	if (IS_ERR(p->rx)) {
		ret = PTR_ERR(p->rx);
		p->rx = NULL;
		return ret;
	}
	return skd840n_idm_rx_layout(p->rx, &p->hw.rx);
}

static void lan4_stop_software(struct lan4_priv *p)
{
	int i;

	WRITE_ONCE(p->running, false);
	lan4_mask(p, true);
	netif_tx_disable(p->ndev);
	cancel_delayed_work_sync(&p->tick);
	for (i = 0; i < p->requested; i++)
		synchronize_irq(p->irqs[i]);
	if (p->napi_on) {
		napi_disable(&p->napi);
		p->napi_on = false;
	}
	skd840n_lan4_block(&p->hw);
	netif_carrier_off(p->ndev);
	/* Do not unmap, free, disable clocks, release pool, or restore old bases. */
}

static int lan4_open(struct net_device *ndev)
{
	struct lan4_priv *p = netdev_priv(ndev);
	int i, ret;

	mutex_lock(&p->control);
	if (p->attempted) {
		ret = -EBUSY;
		netdev_err(ndev, "one activation per cold boot; power-cycle before retry\n");
		goto out;
	}
	WRITE_ONCE(p->error, 0);
	p->hw.stage = "clocks-mdio";
	ret = clk_bulk_prepare_enable(LAN4_CLOCKS, p->clocks);
	if (ret)
		goto out;
	p->clocks_on = true;
	if (!clk_get_rate(p->clocks[7].clk) || clk_get_rate(p->clocks[7].clk) > 2500000) {
		ret = -ERANGE;
		goto rollback;
	}
	ret = reset_control_reset(p->mdio_reset);
	if (!ret)
		ret = lan4_phy(p, false);
	if (ret)
		goto rollback;
	p->hw.stage = "allocate-own-dma";
	ret = lan4_alloc(p);
	if (ret)
		goto rollback;
	p->attempted = true;
	p->retained = true;
	ret = skd840n_lan4_prepare(&p->hw);
	if (ret)
		goto quarantine;
	p->hw.stage = "irq-napi";
	for (i = 0; i < LAN4_IRQS; i++) {
		ret = request_irq(p->irqs[i], lan4_irq, 0, "skd840n-lan4", p);
		if (ret)
			goto quarantine;
		p->requested++;
	}
	p->done = 0;
	p->producer = 0;
	p->consumer = 0;
	netdev_reset_queue(ndev);
	napi_enable(&p->napi);
	p->napi_on = true;
	WRITE_ONCE(p->running, true);
	p->hw.stage = "publish-rx";
	ret = skd840n_idm_rx_publish(p->rx);
	if (ret)
		goto quarantine;
	ret = skd840n_lan4_enable(&p->hw);
	if (ret)
		goto quarantine;
	netif_start_queue(ndev);
	netif_carrier_off(ndev);
	lan4_mask(p, false);
	p->link_due = jiffies;
	schedule_delayed_work(&p->tick, 0);
	netdev_info(ndev, "direct test armed; connect only LAN4 to an isolated 1G peer; limit=%u frames each direction\n",
		    LAN4_PACKET_LIMIT);
	goto out;
quarantine:
	lan4_fault(p, ret);
	lan4_stop_software(p);
	goto out;
rollback:
	lan4_free_unexposed(p);
	clk_bulk_disable_unprepare(LAN4_CLOCKS, p->clocks);
	p->clocks_on = false;
out:
	if (ret && !READ_ONCE(p->error))
		WRITE_ONCE(p->error, ret);
	mutex_unlock(&p->control);
	return ret;
}

static int lan4_stop(struct net_device *ndev)
{
	struct lan4_priv *p = netdev_priv(ndev);

	mutex_lock(&p->control);
	lan4_stop_software(p);
	p->hw.stage = "stopped-memory-retained";
	mutex_unlock(&p->control);
	return 0;
}

static void lan4_stats(struct net_device *ndev, struct rtnl_link_stats64 *s)
{
	struct lan4_priv *p = netdev_priv(ndev);

	s->rx_packets = atomic64_read(&p->rx_packets);
	s->rx_bytes = atomic64_read(&p->rx_bytes);
	s->rx_dropped = atomic64_read(&p->rx_dropped);
	s->tx_packets = atomic64_read(&p->tx_packets);
	s->tx_bytes = atomic64_read(&p->tx_bytes);
	s->tx_dropped = atomic64_read(&p->tx_dropped);
	s->rx_errors = READ_ONCE(p->error) ? 1 : 0;
}

static const struct net_device_ops lan4_netdev_ops = {
	.ndo_open = lan4_open,
	.ndo_stop = lan4_stop,
	.ndo_start_xmit = lan4_xmit,
	.ndo_get_stats64 = lan4_stats,
	.ndo_set_mac_address = eth_mac_addr,
	.ndo_validate_addr = eth_validate_addr,
};

static ssize_t test_state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct lan4_priv *p = dev_get_drvdata(dev);
	ssize_t n;

	mutex_lock(&p->control);
	n = sysfs_emit(buf,
		"version=lan4-direct-recovery-v1 interface=%s stage=%s running=%u error=%d link_error=%d\n"
		"attempted=%u dma_retained=%u carrier=%u packet_limit=%u\n"
		"wait_offset=0x%x wait_value=0x%x irq=%lld rx_consumed=%lld rx_delivered=%lld rx_drop=%lld\n"
		"tx_submitted=%u tx_completed=%u tx_pending=%u tx_drop=%lld\n"
		"rx_transport_drop=%lld release_busy=%lld not_ready=%lld last_flags=0x%x last_len=%u last_queue=%u\n",
		p->ndev->name, p->hw.stage, READ_ONCE(p->running), READ_ONCE(p->error),
		READ_ONCE(p->link_error), p->attempted, p->retained, netif_carrier_ok(p->ndev),
		LAN4_PACKET_LIMIT, p->hw.wait_offset, p->hw.wait_value,
		(long long)atomic64_read(&p->irq_count), (long long)atomic64_read(&p->rx_consumed),
		(long long)atomic64_read(&p->rx_packets), (long long)atomic64_read(&p->rx_dropped),
		READ_ONCE(p->submitted), READ_ONCE(p->completed), READ_ONCE(p->pending),
		(long long)atomic64_read(&p->tx_dropped),
		(long long)atomic64_read(&p->rx_transport_drop),
		(long long)atomic64_read(&p->rx_busy), (long long)atomic64_read(&p->rx_not_ready),
		READ_ONCE(p->last_rx_flags), READ_ONCE(p->last_rx_len), READ_ONCE(p->last_rx_queue));
	mutex_unlock(&p->control);
	return n;
}
static DEVICE_ATTR_RO(test_state);
static struct attribute *lan4_attrs[] = { &dev_attr_test_state.attr, NULL };
ATTRIBUTE_GROUPS(lan4);

static void __iomem *lan4_map(struct platform_device *pdev, const char *name,
			     resource_size_t start, resource_size_t bytes)
{
	struct resource *res = platform_get_resource_byname(pdev, IORESOURCE_MEM, name);

	if (!res || res->start != start || resource_size(res) != bytes)
		return ERR_PTR(-EINVAL);
	return devm_ioremap_resource(&pdev->dev, res);
}

static int lan4_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *mem;
	struct reserved_mem *rmem;
	struct net_device *ndev;
	struct lan4_priv *p;
	int i, ret;

	if (!of_machine_is_compatible("skyworth,sk-d840n"))
		return -ENODEV;
	mem = of_parse_phandle(dev->of_node, "memory-region", 0);
	if (!mem)
		return -EINVAL;
	rmem = of_reserved_mem_lookup(mem);
	ret = !rmem || rmem->base != 0x94000000 || rmem->size != 0x4000000 ||
	      !of_device_is_compatible(mem, "shared-dma-pool") ||
	      !of_property_read_bool(mem, "no-map") ||
	      of_property_read_bool(mem, "reusable") ? -EINVAL : 0;
	of_node_put(mem);
	if (ret)
		return dev_err_probe(dev, ret, "requires dedicated 64 MiB test DMA pool\n");
	ndev = alloc_etherdev(sizeof(*p));
	if (!ndev)
		return -ENOMEM;
	p = netdev_priv(ndev);
	p->hw.dev = dev;
	p->hw.stage = "registered-down";
	p->ndev = ndev;
	SET_NETDEV_DEV(ndev, dev);
	strscpy(ndev->name, "lan4test", IFNAMSIZ);
	ndev->netdev_ops = &lan4_netdev_ops;
	ndev->min_mtu = 68;
	ndev->max_mtu = 1500;
	ndev->features = 0;
	ndev->hw_features = 0;
	eth_hw_addr_random(ndev); /* No stock MAC/identity or persistent writes. */
	netif_carrier_off(ndev);
	mutex_init(&p->control);
	mutex_init(&p->mdio_lock);
	spin_lock_init(&p->tx_lock);
	spin_lock_init(&p->irq_lock);
	INIT_DELAYED_WORK(&p->tick, lan4_tick);
	p->hw.np = lan4_map(pdev, "nppt", 0x19000000, 0x1000000);
	p->hw.pps = lan4_map(pdev, "pps", 0x18000000, 0x1000000);
	p->mdio = lan4_map(pdev, "mdio0", 0x14f01000, 0x1000);
	if (IS_ERR(p->hw.np) || IS_ERR(p->hw.pps) || IS_ERR(p->mdio)) {
		ret = IS_ERR(p->hw.np) ? PTR_ERR(p->hw.np) :
			IS_ERR(p->hw.pps) ? PTR_ERR(p->hw.pps) : PTR_ERR(p->mdio);
		goto free;
	}
	for (i = 0; i < LAN4_CLOCKS; i++)
		p->clocks[i].id = lan4_clocks[i];
	ret = devm_clk_bulk_get(dev, LAN4_CLOCKS, p->clocks);
	if (ret)
		goto free;
	p->mdio_reset = devm_reset_control_get_exclusive(dev, "mdio");
	if (IS_ERR(p->mdio_reset)) {
		ret = PTR_ERR(p->mdio_reset);
		goto free;
	}
	for (i = 0; i < LAN4_IRQS; i++) {
		p->irqs[i] = platform_get_irq(pdev, i);
		if (p->irqs[i] < 0) {
			ret = p->irqs[i];
			goto free;
		}
	}
	ret = of_reserved_mem_device_init(dev);
	if (ret)
		goto free;
	p->pool = true;
	netif_napi_add(ndev, &p->napi, lan4_poll);
	platform_set_drvdata(pdev, p);
	/* Complete all fallible sysfs setup before publishing a live netdev. */
	ret = sysfs_create_groups(&dev->kobj, lan4_groups);
	if (ret)
		goto release_pool;
	ret = register_netdev(ndev);
	if (ret) {
		sysfs_remove_groups(&dev->kobj, lan4_groups);
		goto release_pool;
	}
	dev_info(dev, "LAN4 direct-test netdev registered DOWN; no DMA started; use skd840n-lan4-test start\n");
	return 0;
release_pool:
	platform_set_drvdata(pdev, NULL);
	netif_napi_del(&p->napi);
	of_reserved_mem_device_release(dev);
free:
	free_netdev(ndev);
	return dev_err_probe(dev, ret, "LAN4 test probe failed\n");
}

static void lan4_shutdown(struct platform_device *pdev)
{
	struct lan4_priv *p = platform_get_drvdata(pdev);

	if (!p || !p->retained)
		return;
	rtnl_lock();
	mutex_lock(&p->control);
	lan4_stop_software(p);
	mutex_unlock(&p->control);
	rtnl_unlock();
}

static const struct of_device_id lan4_match[] = {
	{ .compatible = "skyworth,sk-d840n-lan4-direct" },
	{ }
};

static struct platform_driver lan4_driver = {
	.probe = lan4_probe,
	.shutdown = lan4_shutdown,
	.driver = {
		.name = "skd840n-lan4-direct",
		.of_match_table = lan4_match,
		.suppress_bind_attrs = true,
	},
};
builtin_platform_driver(lan4_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("SK-D840N one-shot LAN4 CPU-direct RAM experiment");
