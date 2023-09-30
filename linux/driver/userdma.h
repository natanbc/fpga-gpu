#include <linux/ioctl.h>

#ifndef USER_DMA_H
#define USER_DMA_H

struct userdma_buf_creation_data {
    /* in */  unsigned int size;
    /* out */ void* phys_addr;
};

struct userdma_phys_addr_data {
    /* in */ int fd;
    /* out */ void* phys_addr;
};

#define USERDMA_IOCTL_PRINT    _IOR( 'u', 1, int)
#define USERDMA_IOCTL_ALLOC    _IOWR('u', 2, struct userdma_buf_creation_data)
#define USERDMA_IOCTL_GET_PHYS _IOWR('u', 3, struct userdma_phys_addr_data)

#endif