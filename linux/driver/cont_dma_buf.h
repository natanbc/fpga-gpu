#ifndef CONT_DMA_BUF_H
#define CONT_DMA_BUF_H

#include <linux/device.h>

long cont_dmabuf_alloc(struct device *dev, unsigned long size, void** phys_addr);

#endif

