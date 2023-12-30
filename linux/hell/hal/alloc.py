import array
import fcntl
import os

from .dmabuf import DmaBuf


__all__ = ["Alloc"]


USERDMA_IOCTL_ALLOC = 0xc0087502


class Alloc:
    def __init__(self):
        self._fd = os.open("/dev/userdma", os.O_RDWR)

    def alloc(self, size: int) -> (DmaBuf, int):
        buf = array.array('i', [size, 0])
        res = fcntl.ioctl(self._fd, USERDMA_IOCTL_ALLOC, buf)
        if res < 0:
            raise ValueError(f"failed to allocate: {res}")
        return DmaBuf(res, size), buf[1]

    def __del__(self):
        os.close(self._fd)
