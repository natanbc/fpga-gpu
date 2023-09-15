#include <linux/device.h>
#include <linux/dma-buf.h>
#include <linux/dma-map-ops.h>
#include <linux/dma-resv.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/printk.h>

// Based on https://github.com/torvalds/linux/blob/9fdfb15a3dbf818e06be514f4abbfc071004cbe7/drivers/dma-buf/udmabuf.c,
// modified to allocate contiguous memory.

#define DMA_GFP   (GFP_KERNEL)
#define DMA_ATTRS (DMA_ATTR_WEAK_ORDERING|DMA_ATTR_WRITE_COMBINE|DMA_ATTR_FORCE_CONTIGUOUS)

struct cont_buf {
    pgoff_t pagecount;
    void* cpu_addr;
    dma_addr_t dma_addr;
    struct sg_table *sg;
    struct device *dev;
};

static vm_fault_t cbuf_vm_fault(struct vm_fault *vmf) {
    struct vm_area_struct *vma = vmf->vma;
    struct cont_buf *cbuf = vma->vm_private_data;
    pgoff_t pgoff = vmf->pgoff;

    if(pgoff >= cbuf->pagecount) {
        return VM_FAULT_SIGBUS;
    }
    vmf->page = vmalloc_to_page(((char*)cbuf->cpu_addr) + (pgoff << PAGE_SHIFT));
    get_page(vmf->page);
    return 0;
}

static const struct vm_operations_struct cbuf_vm_ops = {
    .fault = cbuf_vm_fault,
};

static int mmap_cbuf(struct dma_buf *buf, struct vm_area_struct *vma) {
    struct cont_buf *cbuf = buf->priv;

    if((vma->vm_flags & (VM_SHARED | VM_MAYSHARE)) == 0) {
        return -EINVAL;
    }

    vma->vm_ops = &cbuf_vm_ops;
    vma->vm_private_data = cbuf;
    return 0;
}

//static int vmap_cbuf(struct dma_buf *buf, struct iosys_map *map) {
//    struct cont_buf *cbuf = buf->priv;
//    void *vaddr;
//
//    dma_resv_assert_held(buf->resv);
//
//    vaddr = vm_map_ram(cbuf->pages, cbuf->pagecount, -1);
//    if(!vaddr) {
//        return -EINVAL;
//    }
//
//    iosys_map_set_vaddr(map, vaddr);
//    return 0;
//}
//
//static void vunmap_cbuf(struct dma_buf *buf, struct iosys_map *map) {
//    struct cont_buf *cbuf = buf->priv;
//
//    dma_resv_assert_held(buf->resv);
//
//    vm_unmap_ram(map->vaddr, cbuf->pagecount);
//}

static struct sg_table *get_sg_table(struct device *dev, struct dma_buf *buf,
                     enum dma_data_direction direction) {
    struct cont_buf *cbuf = buf->priv;
    struct sg_table *sg;
    int ret;

    sg = kzalloc(sizeof(*sg), GFP_KERNEL);
    if(!sg) {
        return ERR_PTR(-ENOMEM);
    }
    ret = sg_alloc_table(sg, 1, GFP_KERNEL);
    if(ret < 0) {
        goto err;
    }
    sg_init_one(sg->sgl, cbuf->cpu_addr, cbuf->pagecount << PAGE_SHIFT);
    ret = dma_map_sgtable(dev, sg, direction, 0);
    if(ret < 0) {
        goto err;
    }
    return sg;

err:
    sg_free_table(sg);
    kfree(sg);
    return ERR_PTR(ret);
}

static void put_sg_table(struct device *dev, struct sg_table *sg,
             enum dma_data_direction direction) {
    dma_unmap_sgtable(dev, sg, direction, 0);
    sg_free_table(sg);
    kfree(sg);
}

static struct sg_table *map_cbuf(struct dma_buf_attachment *at,
                    enum dma_data_direction direction) {
    return get_sg_table(at->dev, at->dmabuf, direction);
}

static void unmap_cbuf(struct dma_buf_attachment *at,
              struct sg_table *sg,
              enum dma_data_direction direction) {
    put_sg_table(at->dev, sg, direction);
}

static void release_cbuf(struct dma_buf *buf) {
    struct cont_buf *cbuf = buf->priv;
    struct device *dev = cbuf->dev;

    if(cbuf->sg) {
        put_sg_table(dev, cbuf->sg, DMA_BIDIRECTIONAL);
    }

    dma_free_attrs(dev, cbuf->pagecount << PAGE_SHIFT, cbuf->cpu_addr, cbuf->dma_addr, DMA_ATTRS);
    kfree(cbuf);
}

static int begin_cpu_cbuf(struct dma_buf *buf,
                 enum dma_data_direction direction) {
    struct cont_buf *cbuf = buf->priv;
    struct device *dev = cbuf->dev;
    int ret = 0;

    if(!cbuf->sg) {
        cbuf->sg = get_sg_table(dev, buf, direction);
        if(IS_ERR(cbuf->sg)) {
            ret = PTR_ERR(cbuf->sg);
            cbuf->sg = NULL;
        }
    } else {
        dma_sync_sg_for_cpu(dev, cbuf->sg->sgl, cbuf->sg->nents,
                    direction);
    }

    return ret;
}

static int end_cpu_cbuf(struct dma_buf *buf,
               enum dma_data_direction direction) {
    struct cont_buf *cbuf = buf->priv;
    struct device *dev = cbuf->dev;

    if(!cbuf->sg) {
        return -EINVAL;
    }

    dma_sync_sg_for_device(dev, cbuf->sg->sgl, cbuf->sg->nents, direction);
    return 0;
}


static const struct dma_buf_ops dmabuf_ops = {
    .cache_sgt_mapping = true,
    .map_dma_buf       = map_cbuf,
    .unmap_dma_buf     = unmap_cbuf,
    .release           = release_cbuf,
    .mmap              = mmap_cbuf,
//    .vmap              = vmap_cbuf,
//    .vunmap            = vunmap_cbuf,
    .begin_cpu_access  = begin_cpu_cbuf,
    .end_cpu_access    = end_cpu_cbuf,
};

long cont_dmabuf_alloc(struct device *dev, unsigned long size, void** phys_addr) {
    DEFINE_DMA_BUF_EXPORT_INFO(exp_info);
    long ret;
    struct cont_buf *cbuf;
    struct dma_buf *dmabuf;
    int fd;

    ret = 0;
    cbuf = NULL;
    dmabuf = NULL;

    if(size == 0 || (size & ((1 << PAGE_SHIFT) - 1))) {
        return -EINVAL;
    }

    cbuf = kzalloc(sizeof(*cbuf), GFP_KERNEL);
    if(!cbuf) {
        return -ENOMEM;
    }
    cbuf->dev = dev;
    cbuf->pagecount = size >> PAGE_SHIFT;

    cbuf->cpu_addr = dma_alloc_attrs(dev, cbuf->pagecount << PAGE_SHIFT, &cbuf->dma_addr, DMA_GFP, DMA_ATTRS);
    if(!cbuf->cpu_addr) {
        ret = -ENOMEM;
        goto err;
    }
    *phys_addr = (void*)cbuf->dma_addr;

    exp_info.ops  = &dmabuf_ops;
    exp_info.size = cbuf->pagecount << PAGE_SHIFT;
    exp_info.priv = cbuf;
    exp_info.flags = O_RDWR;

    dmabuf = dma_buf_export(&exp_info);
    if(IS_ERR(dmabuf)) {
        ret = PTR_ERR(dmabuf);
        goto err;
    }

    fd = dma_buf_fd(dmabuf, O_CLOEXEC);
    if(fd < 0) {
        ret = fd;
        goto err;
    }

    return fd;

err:
    if(dmabuf) {
        dma_buf_put(dmabuf);
    }
    if(cbuf->dma_addr) {
        dma_free_attrs(dev, cbuf->pagecount << PAGE_SHIFT, cbuf->cpu_addr, cbuf->dma_addr, DMA_ATTRS);
    }
    kfree(cbuf);
    return ret;
}

