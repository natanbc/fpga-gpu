#define pr_fmt(fmt) "%s:%s: " fmt, KBUILD_MODNAME, __func__

#include <linux/device.h>
#include <linux/dma-buf.h>
#include <linux/dma-map-ops.h>
#include <linux/dma-resv.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/printk.h>
#include "cont_dma_buf.h"
#include "userdma.h"

static long print_dmabuf_info(struct device *dev, struct sg_table* sg_tbl, void* unused) {
    struct scatterlist *sglist, *sg;
    unsigned int nents;
    int count, i;
    (void)unused;

    sglist = sg_tbl->sgl;
    nents = sg_tbl->nents;

    pr_info("sg->nents = %d\n", nents);

    count = dma_map_sg(dev, sglist, nents, DMA_TO_DEVICE);

    pr_info("count = %d\n", count);

    for_each_sg(sglist, sg, count, i) {
        pr_info("[%d].addr = 0x%08lx\n", i, sg_dma_address(sg));
        pr_info("[%d].len  = %dKiB\n", i, sg_dma_len(sg) >> 10);
    }

    dma_unmap_sg(dev, sglist, nents, DMA_TO_DEVICE);

    return 0;
}

static long get_dmabuf_addr(struct device* dev, struct sg_table* sg_tbl, void* ctx) {
    struct scatterlist *sglist, *sg;
    unsigned int nents;
    int count, i;
    unsigned long* res;
    res = ctx;

    sglist = sg_tbl->sgl;
    nents = sg_tbl->nents;

    count = dma_map_sg(dev, sglist, nents, DMA_TO_DEVICE);

    if(count != 1) {
        return -EINVAL;
    }

    for_each_sg(sglist, sg, count, i) {
        *res = sg_dma_address(sg);
    }

    dma_unmap_sg(dev, sglist, nents, DMA_TO_DEVICE);

    return 0;
}

static long with_sg(struct device *dev, int buf_fd, long (*action)(struct device*, struct sg_table*, void*), void* ctx) {
    long ret;
    struct dma_buf *buf;
    struct dma_buf_attachment *at;
    struct sg_table *sg_tbl;

    ret = 0;
    pr_info("Getting dma-buf from fd %d\n", buf_fd);

    buf = dma_buf_get(buf_fd);
    if(IS_ERR(buf)) {
        ret = PTR_ERR(buf);
        pr_err("dma_buf_get failed: %ld\n", ret);
        goto exit;
    }

    at = dma_buf_attach(buf, dev);
    if(IS_ERR(at)) {
        ret = PTR_ERR(at);
        pr_err("dma_buf_attach failed: %ld\n", ret);
        goto put_buf;
    }

    sg_tbl = dma_buf_map_attachment(at, DMA_TO_DEVICE);
    if(IS_ERR(sg_tbl)) {
        ret = PTR_ERR(sg_tbl);
        pr_err("dma_buf_map_attachment failed: %ld\n", ret);
        goto detach;
    }

    ret = action(dev, sg_tbl, ctx);

//unmap:
    dma_buf_unmap_attachment(at, sg_tbl, DMA_TO_DEVICE);
detach:
    dma_buf_detach(buf, at);
put_buf:
    dma_buf_put(buf);
exit:
    return ret;
}

static long ioctl_print(struct device *dev, int buf_fd) {
    return with_sg(dev, buf_fd, print_dmabuf_info, 0);
}

static long ioctl_alloc_dmabuf(struct device* dev, unsigned long arg) {
    struct userdma_buf_creation_data d;
    long res;
    pr_warn("Use /dev/dma_heap instead");
    if(copy_from_user(&d, (const void __user*)arg, sizeof(d))) {
        return -EFAULT;
    }
    res = cont_dmabuf_alloc(dev, d.size, &d.phys_addr);
    if(res < 0) {
        return res;
    }
    if(copy_to_user((void __user*)arg, &d, sizeof(d))) {
        return -EFAULT;
    }
    return res;
}

static long ioctl_get_phys(struct device* dev, unsigned long arg) {
    struct userdma_phys_addr_data d;
    long res;
    if(copy_from_user(&d, (const void __user*)arg, sizeof(d))) {
        return -EFAULT;
    }
    res = with_sg(dev, d.fd, get_dmabuf_addr, &d.phys_addr);
    if(res < 0) {
        return res;
    }
    if(copy_to_user((void __user*)arg, &d, sizeof(d))) {
        return -EFAULT;
    }
    return res;

}

static long handle_ioctl(struct file *filp, unsigned int ioctl,
              unsigned long arg) {
    struct device *dev;
    dev = ((struct miscdevice*)filp->private_data)->this_device;

    switch(ioctl) {
        case USERDMA_IOCTL_PRINT:
            return ioctl_print(dev, (int)arg);
        case USERDMA_IOCTL_ALLOC:
            return ioctl_alloc_dmabuf(dev, arg);
        case USERDMA_IOCTL_GET_PHYS:
            return ioctl_get_phys(dev, arg);
        default:
            return -EINVAL;
    }
}

static const struct file_operations userdma_fops = {
    .owner      = THIS_MODULE,
    .unlocked_ioctl = handle_ioctl,
};

static struct miscdevice userdma_misc = {
    .minor          = MISC_DYNAMIC_MINOR,
    .name           = "userdma",
    .fops           = &userdma_fops,
};

static int __init userdma_dev_init(void)
{
    int ret = 0;

    ret = misc_register(&userdma_misc);
    if (ret < 0) {
        pr_err("Could not initialize userdma device\n");
        goto err;
    }

    ret = dma_coerce_mask_and_coherent(userdma_misc.this_device, DMA_BIT_MASK(32));
    if (ret < 0) {
        pr_err("Could not setup DMA mask for userdma device\n");
        goto deregister;
    }
    return 0;

deregister:
    misc_deregister(&userdma_misc);
err:
    return ret;
}

static void __exit userdma_dev_exit(void)
{
    misc_deregister(&userdma_misc);
}

module_init(userdma_dev_init)
module_exit(userdma_dev_exit)

MODULE_AUTHOR("natanbc");
MODULE_LICENSE("GPL");
MODULE_IMPORT_NS(DMA_BUF);
