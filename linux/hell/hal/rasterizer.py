import asyncio

from .mmio import u32
from .uio import Uio


__all__ = ["Rasterizer"]


IRQ_STATUS = slice(0x00, 0x04)
IRQ_MASK = slice(0x04, 0x08)
FB_BASE = slice(0x08, 0x0C)
Z_BASE = slice(0x0C, 0x10)
IDLE = slice(0x10, 0x14)
CMD_ADDR_64 = slice(0x14, 0x18)
CMD_WORDS = slice(0x18, 0x1C)
CMD_CTRL = slice(0x1C, 0x20)
CMD_DMA_IDLE = slice(0x20, 0x24)
CMD_IDLE = slice(0x24, 0x28)


class Rasterizer:
    def __init__(self, uio: Uio):
        self._uio = uio
        self._map = uio.map(0)
        self._cmd_done = asyncio.Event()
        self._cmd_dma_done = asyncio.Event()

        asyncio.get_event_loop().create_task(self._handle_irq())

        self._map[IRQ_MASK] = u32(0b11)

    async def wait_cmd_dma(self):
        await self._cmd_dma_done.wait()
        self._cmd_dma_done.clear()

    async def wait_cmd(self):
        await self._cmd_done.wait()
        self._cmd_done.clear()

    def submit_command(self, buffer: int, words: int):
        assert u32(self._map[CMD_DMA_IDLE]) == 1
        assert buffer & 0x3F == 0
        self._cmd_done.clear()
        self._cmd_dma_done.clear()

        self._map[CMD_ADDR_64] = u32(buffer >> 6)
        self._map[CMD_WORDS] = u32(words)
        self._map[CMD_CTRL] = u32(u32(self._map[CMD_CTRL]) ^ 1)

    def set_buffers(self, fb: int, zb: int):
        assert fb & 0x7f == 0
        assert zb & 0x7f == 0
        self._map[FB_BASE] = u32(fb)
        self._map[Z_BASE] = u32(zb)

    async def _handle_irq(self):
        while True:
            self._uio.enable_irq()
            await self._uio.wait_irq()

            irq_status = u32(self._map[IRQ_STATUS])
            self._map[IRQ_STATUS] = u32(irq_status)

            if irq_status & 0b01:
                self._cmd_done.set()
            if irq_status & 0b10:
                self._cmd_dma_done.set()

