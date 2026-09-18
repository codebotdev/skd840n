// SPDX-License-Identifier: GPL-2.0-only
/*
 * SK-D840N normal RX transport, not a complete NP/MAC driver.
 * Stock np_133.ko normal-poll/BP/release analysis: docs/IDM-RX.md.
 * No initcall, probe, netdev, legacy-buffer adoption, or PPU/SMAC writes.
 */
#include <linux/bitops.h>
#include <linux/compiler.h>
#include <linux/dma-mapping.h>
#include <linux/err.h>
#include <linux/hash.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/slab.h>
#include <linux/string.h>

#include "skd840n-idm-rx.h"
#include "skd840n-idm-ring-math.h"

#define SKD840N_RX_HASH_BITS 13
#define SKD840N_RX_HASH_SIZE (1U << SKD840N_RX_HASH_BITS)
#define SKD840N_RX_BUFFERS (SKD840N_IDM_RX_BUFFERS + SKD840N_IDM_RX_JUMBO_BUFFERS)
#define SKD840N_RX_DESC_SIZE (SKD840N_IDM_RX_QUEUES * SKD840N_IDM_DESC_DEPTH * \
			     SKD840N_IDM_DESC_BYTES)
#define SKD840N_NORMAL_BP_SIZE (SKD840N_IDM_RX_BP_DEPTH * sizeof(__be32))
#define SKD840N_JUMBO_BP_SIZE (SKD840N_IDM_RX_JUMBO_BP_DEPTH * sizeof(__be32))

struct skd840n_rx_buffer {
	struct hlist_node hash;
	void *cpu;
	dma_addr_t dma;
	u32 bytes;
	bool mapped;
	bool jumbo;
	bool posted;
};

struct skd840n_idm_rx {
	struct device *dev;
	void __iomem *idm;
	struct skd840n_idm_rx_ops ops;
	void *priv;
	__le32 *descriptors;
	dma_addr_t desc_dma;
	__be32 *normal_bp;
	dma_addr_t normal_dma;
	__be32 *jumbo_bp;
	dma_addr_t jumbo_dma;
	struct skd840n_rx_buffer *buffers;
	struct hlist_head *lookup;
	struct skd840n_rx_buffer *pending[SKD840N_IDM_RX_QUEUES];
	u16 consumer[SKD840N_IDM_RX_QUEUES];
	u16 normal_producer;
	u16 jumbo_producer;
	u8 cursor;
	bool exposed;
	bool running;
	bool stopping;
	int fault;
	struct skd840n_idm_rx_stats stats;
};

static int skd840n_rx_fault(struct skd840n_idm_rx *rx, int error)
{
	if (!rx->fault) {
		rx->fault = error;
		rx->stats.faults++;
	}
	/* Only prevents RX interrupt storms; this does NOT stop DMA. */
	writel(readl(rx->idm + SKD840N_IDM_INT_MASK) | 0x00ffffff,
	       rx->idm + SKD840N_IDM_INT_MASK);
	return rx->fault;
}

/* Only allocation rollback or destruction AFTER successful quiescence. */
static void skd840n_rx_free(struct skd840n_idm_rx *rx)
{
	u32 i;

	if (rx->buffers) {
		for (i = 0; i < SKD840N_RX_BUFFERS; i++) {
			struct skd840n_rx_buffer *b = &rx->buffers[i];

			if (b->mapped)
				dma_unmap_single(rx->dev, b->dma, b->bytes,
						 DMA_FROM_DEVICE);
			kfree(b->cpu);
		}
	}
	if (rx->jumbo_bp)
		dma_free_coherent(rx->dev, SKD840N_JUMBO_BP_SIZE,
				  rx->jumbo_bp, rx->jumbo_dma);
	if (rx->normal_bp)
		dma_free_coherent(rx->dev, SKD840N_NORMAL_BP_SIZE,
				  rx->normal_bp, rx->normal_dma);
	if (rx->descriptors)
		dma_free_coherent(rx->dev, SKD840N_RX_DESC_SIZE,
				  rx->descriptors, rx->desc_dma);
	kvfree(rx->buffers);
	kvfree(rx->lookup);
	kfree(rx);
}

static struct skd840n_rx_buffer *skd840n_rx_lookup(struct skd840n_idm_rx *rx,
						u32 dma)
{
	struct skd840n_rx_buffer *b;
	u32 slot = hash_32(dma, SKD840N_RX_HASH_BITS);

	hlist_for_each_entry(b, &rx->lookup[slot], hash)
		if (b->dma == dma)
			return b;
	return NULL;
}

struct skd840n_idm_rx *
skd840n_idm_rx_alloc(struct device *dev, void __iomem *idm,
		    const struct skd840n_idm_rx_ops *ops, void *priv)
{
	struct skd840n_idm_rx *rx;
	u32 i;
	int ret;

	if (!dev || !idm || !ops || !ops->receive || !ops->quiesce)
		return ERR_PTR(-EINVAL);
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return ERR_PTR(ret);
	rx = kzalloc(sizeof(*rx), GFP_KERNEL);
	if (!rx)
		return ERR_PTR(-ENOMEM);
	rx->dev = dev;
	rx->idm = idm;
	rx->ops = *ops;
	rx->priv = priv;
	rx->buffers = kvcalloc(SKD840N_RX_BUFFERS, sizeof(*rx->buffers), GFP_KERNEL);
	rx->lookup = kvcalloc(SKD840N_RX_HASH_SIZE, sizeof(*rx->lookup), GFP_KERNEL);
	if (!rx->buffers || !rx->lookup)
		goto no_memory;

	rx->descriptors = dma_alloc_coherent(dev, SKD840N_RX_DESC_SIZE,
					     &rx->desc_dma, GFP_KERNEL);
	rx->normal_bp = dma_alloc_coherent(dev, SKD840N_NORMAL_BP_SIZE,
					  &rx->normal_dma, GFP_KERNEL);
	rx->jumbo_bp = dma_alloc_coherent(dev, SKD840N_JUMBO_BP_SIZE,
					 &rx->jumbo_dma, GFP_KERNEL);
	if (!rx->descriptors || !rx->normal_bp || !rx->jumbo_bp)
		goto no_memory;
	ret = skd840n_idm_buffer_range(rx->desc_dma, SKD840N_RX_DESC_SIZE);
	if (!ret)
		ret = skd840n_idm_buffer_range(rx->normal_dma, SKD840N_NORMAL_BP_SIZE);
	if (!ret)
		ret = skd840n_idm_buffer_range(rx->jumbo_dma, SKD840N_JUMBO_BP_SIZE);
	if (ret)
		goto fail;
	memset(rx->descriptors, 0, SKD840N_RX_DESC_SIZE);
	memset(rx->normal_bp, 0, SKD840N_NORMAL_BP_SIZE);
	memset(rx->jumbo_bp, 0, SKD840N_JUMBO_BP_SIZE);

	for (i = 0; i < SKD840N_RX_BUFFERS; i++) {
		struct skd840n_rx_buffer *b = &rx->buffers[i];
		u32 key;

		b->jumbo = i >= SKD840N_IDM_RX_BUFFERS;
		b->bytes = b->jumbo ? SKD840N_IDM_JUMBO_BYTES : SKD840N_IDM_BUFFER_BYTES;
		b->cpu = kmalloc(b->bytes, GFP_KERNEL);
		if (!b->cpu)
			goto no_memory;
		b->dma = dma_map_single(dev, b->cpu, b->bytes, DMA_FROM_DEVICE);
		if (dma_mapping_error(dev, b->dma)) {
			ret = -EIO;
			goto fail;
		}
		b->mapped = true;
		ret = skd840n_idm_buffer_range(b->dma, b->bytes);
		if (ret)
			goto fail;
		key = lower_32_bits(b->dma);
		if (skd840n_rx_lookup(rx, key)) {
			ret = -EEXIST;
			goto fail;
		}
		hlist_add_head(&b->hash, &rx->lookup[hash_32(key, SKD840N_RX_HASH_BITS)]);
		if (b->jumbo)
			rx->jumbo_bp[i - SKD840N_IDM_RX_BUFFERS] = cpu_to_be32(key);
		else
			rx->normal_bp[i] = cpu_to_be32(key);
	}
	rx->normal_producer = SKD840N_IDM_RX_BUFFERS;
	rx->jumbo_producer = SKD840N_IDM_RX_JUMBO_BUFFERS;
	return rx;

no_memory:
	ret = -ENOMEM;
fail:
	skd840n_rx_free(rx);
	return ERR_PTR(ret);
}

int skd840n_idm_rx_layout(struct skd840n_idm_rx *rx,
			struct skd840n_idm_rx_layout *layout)
{
	if (!rx || !layout || rx->exposed)
		return -EINVAL;
	*layout = (struct skd840n_idm_rx_layout) {
		.descriptors = rx->desc_dma,
		.normal_bp = rx->normal_dma,
		.jumbo_bp = rx->jumbo_dma,
		.queues = SKD840N_IDM_RX_QUEUES,
		.depth = SKD840N_IDM_DESC_DEPTH,
		.normal_depth = SKD840N_IDM_RX_BP_DEPTH,
		.jumbo_depth = SKD840N_IDM_RX_JUMBO_BP_DEPTH,
		.normal_count = SKD840N_IDM_RX_BUFFERS,
		.jumbo_count = SKD840N_IDM_RX_JUMBO_BUFFERS,
		.normal_bytes = SKD840N_IDM_BUFFER_BYTES,
		.jumbo_bytes = SKD840N_IDM_JUMBO_BYTES,
		.headroom = SKD840N_IDM_RX_HEADROOM,
	};
	/* Conservatively assume the owner may publish the copied addresses. */
	rx->exposed = true;
	return 0;
}

int skd840n_idm_rx_publish(struct skd840n_idm_rx *rx)
{
	u32 remaining = SKD840N_IDM_RX_BUFFERS;
	u32 i, count, word;
	int ret;

	if (!rx || !rx->exposed || rx->running || rx->fault || rx->stopping)
		return -EINVAL;
	for (i = 0; i < SKD840N_RX_BUFFERS; i++)
		rx->buffers[i].posted = true;
	rx->running = true;
	/* Coherence alone does not order BP memory before MMIO credits. */
	dma_wmb();
	while (remaining) {
		count = min_t(u32, remaining, 1024);
		ret = skd840n_idm_refill_word(count, 0, &word);
		if (ret)
			return skd840n_rx_fault(rx, ret);
		writel(word, rx->idm + SKD840N_IDM_BP_REFILL);
		remaining -= count;
	}
	ret = skd840n_idm_refill_word(0, SKD840N_IDM_RX_JUMBO_BUFFERS, &word);
	if (ret)
		return skd840n_rx_fault(rx, ret);
	writel(word, rx->idm + SKD840N_IDM_BP_REFILL);
	return 0;
}

/* One credit per queue can be pending; never repeat delivery after -EAGAIN. */
static int skd840n_rx_release(struct skd840n_idm_rx *rx, u32 queue)
{
	struct skd840n_rx_buffer *b = rx->pending[queue];
	u32 word, refill;
	int ret;

	if (!b)
		return 0;
	if (readl(rx->idm + SKD840N_IDM_RX_RELEASE) & BIT(31)) {
		rx->stats.release_busy++;
		return -EAGAIN;
	}
	if (b->posted)
		return skd840n_rx_fault(rx, -EUCLEAN);
	ret = skd840n_idm_release_word(queue, 1, &word);
	if (!ret)
		ret = skd840n_idm_refill_word(!b->jumbo, b->jumbo, &refill);
	if (ret)
		return skd840n_rx_fault(rx, ret);
	dma_wmb();
	writel(word, rx->idm + SKD840N_IDM_RX_RELEASE);

	/* Release this descriptor before making its buffer available again. */
	if (b->jumbo) {
		WRITE_ONCE(rx->jumbo_bp[rx->jumbo_producer],
			   cpu_to_be32(lower_32_bits(b->dma)));
		rx->jumbo_producer = (rx->jumbo_producer + 1) &
				     (SKD840N_IDM_RX_JUMBO_BP_DEPTH - 1);
	} else {
		WRITE_ONCE(rx->normal_bp[rx->normal_producer],
			   cpu_to_be32(lower_32_bits(b->dma)));
		rx->normal_producer = (rx->normal_producer + 1) &
				      (SKD840N_IDM_RX_BP_DEPTH - 1);
	}
	b->posted = true;
	rx->pending[queue] = NULL;
	dma_wmb();
	writel(refill, rx->idm + SKD840N_IDM_BP_REFILL);
	return 0;
}

static int skd840n_rx_one(struct skd840n_idm_rx *rx, u32 queue)
{
	struct skd840n_rx_buffer *b;
	__le32 *desc = rx->descriptors + SKD840N_IDM_WORDS *
		(queue * SKD840N_IDM_DESC_DEPTH + rx->consumer[queue]);
	u32 words[SKD840N_IDM_WORDS], address, length, i;
	int ret;

	address = le32_to_cpu(READ_ONCE(desc[0]));
	if (!address) {
		rx->stats.not_ready++;
		return -EAGAIN;
	}
	dma_rmb();
	words[0] = address;
	for (i = 1; i < SKD840N_IDM_WORDS; i++)
		words[i] = le32_to_cpu(READ_ONCE(desc[i]));
	/* A DMA address is a lookup key, NEVER an arbitrary CPU pointer. */
	b = skd840n_rx_lookup(rx, address);
	if (!b || !b->posted ||
	    b->jumbo != !!(words[1] & SKD840N_RX_ADDR_TYPE_MASK))
		return skd840n_rx_fault(rx, -EUCLEAN);
	b->posted = false;
	ret = skd840n_idm_frame_range(words[1], b->bytes, &length);
	if (!ret) {
		dma_sync_single_for_cpu(rx->dev, b->dma, b->bytes, DMA_FROM_DEVICE);
		ret = rx->ops.receive(rx->priv,
				      (u8 *)b->cpu + SKD840N_IDM_RX_HEADROOM,
				      length, words, queue);
		dma_sync_single_for_device(rx->dev, b->dma, b->bytes, DMA_FROM_DEVICE);
	}
	if (ret)
		rx->stats.dropped++;
	else
		rx->stats.delivered++;
	WRITE_ONCE(desc[0], cpu_to_le32(0));
	rx->consumer[queue] = (rx->consumer[queue] + 1) &
			      (SKD840N_IDM_DESC_DEPTH - 1);
	rx->pending[queue] = b;
	rx->stats.consumed++;
	return 0;
}

int skd840n_idm_rx_poll(struct skd840n_idm_rx *rx, int budget, bool *more)
{
	u32 visited, queue, reg, count, limit, i;
	int ret, work = 0;

	if (!rx || !rx->running || budget < 0 || !more)
		return -EINVAL;
	*more = false;
	if (rx->fault)
		return rx->fault;
	if (!budget)
		return 0;
	for (visited = 0; visited < SKD840N_IDM_RX_QUEUES && work < budget; visited++) {
		queue = rx->cursor;
		rx->cursor = (queue + 1) % SKD840N_IDM_RX_QUEUES;
		ret = skd840n_rx_release(rx, queue);
		if (ret == -EAGAIN) {
			*more = true;
			continue;
		}
		if (ret)
			return ret;
		ret = skd840n_idm_count_reg(queue, &reg);
		if (ret)
			return skd840n_rx_fault(rx, ret);
		ret = skd840n_idm_count_value(readl(rx->idm + reg), queue, &count);
		if (ret)
			return skd840n_rx_fault(rx, ret);
		limit = min_t(u32, count, SKD840N_IDM_RX_QUANTUM);
		limit = min_t(u32, limit, budget - work);
		if (limit < count)
			*more = true;
		for (i = 0; i < limit; i++) {
			ret = skd840n_rx_one(rx, queue);
			if (ret == -EAGAIN) {
				*more = true;
				break;
			}
			if (ret)
				return ret;
			work++;
			ret = skd840n_rx_release(rx, queue);
			if (ret == -EAGAIN) {
				*more = true;
				break;
			}
			if (ret)
				return ret;
		}
	}
	if (work == budget)
		*more = true;
	return work;
}

void skd840n_idm_rx_get_stats(struct skd840n_idm_rx *rx,
			     struct skd840n_idm_rx_stats *stats)
{
	if (rx && stats)
		*stats = rx->stats;
}

int skd840n_idm_rx_destroy(struct skd840n_idm_rx **handle)
{
	struct skd840n_idm_rx *rx;
	int ret;

	if (!handle || !*handle)
		return -EINVAL;
	rx = *handle;
	rx->stopping = true;
	rx->running = false;
	if (rx->exposed) {
		/* Parent also handles partial setup with clocks not yet enabled. */
		ret = rx->ops.quiesce(rx->priv);
		if (ret)
			return ret;
	}
	skd840n_rx_free(rx);
	*handle = NULL;
	return 0;
}
