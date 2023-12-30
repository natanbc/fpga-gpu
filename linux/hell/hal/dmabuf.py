import array
import fcntl
import mmap
import os


__all__ = ["DmaBuf"]


DMA_BUF_IOCTL_SYNC = 0x40086200
DMA_BUF_SYNC_READ = 0x01
DMA_BUF_SYNC_WRITE = 0x02
DMA_BUF_SYNC_RW = DMA_BUF_SYNC_READ | DMA_BUF_SYNC_WRITE
DMA_BUF_SYNC_START = 0
DMA_BUF_SYNC_END = 0x04


class DmaBuf:
    def __init__(self, fd: int, size: int):
        self._fd = fd
        self._size = size

    def __del__(self):
        os.close(self._fd)

    @property
    def size(self):
        return self._size

    def map(self):
        return mmap.mmap(self._fd, self._size)

    def sync_start(self):
        self._sync(DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW)

    def sync_end(self):
        self._sync(DMA_BUF_SYNC_END | DMA_BUF_SYNC_RW)

    def _sync(self, flags: int):
        buf = array.array('Q', [flags])
        res = fcntl.ioctl(self._fd, DMA_BUF_IOCTL_SYNC, buf)
        if res != 0:
            raise ValueError(f"dmabuf sync failed: {res}")
