import asyncio
from .uio import Uio
from .mmio import u32


__all__ = ["DisplayController"]


IRQ_STATUS = slice(0x00, 0x04)
IRQ_MASK = slice(0x04, 0x08)
WIDTH = slice(0x08, 0x0C)
HEIGHT = slice(0x0C, 0x10)
PAGE_ADDR = slice(0x10, 0x14)
WORDS = slice(0x14, 0x18)
CTRL = slice(0x18, 0x1C)


class DisplayController:
    def __init__(self, uio: Uio):
        self._uio = uio
        self._map = uio.map(0)
        self._draw_done = asyncio.Event()
        asyncio.get_event_loop().create_task(self._handle_irq())

        fb_size = self.width * self.height * 3
        assert fb_size % 8 == 0
        self._map[CTRL] = u32(0)
        self._map[WORDS] = u32(fb_size // 8)
        self._map[IRQ_MASK] = u32(0b10)

    def __del__(self):
        self._map[CTRL] = u32(0)

    @property
    def width(self) -> int:
        return u32(self._map[WIDTH])

    @property
    def height(self) -> int:
        return u32(self._map[HEIGHT])

    async def wait_end_of_frame(self):
        await self._draw_done.wait()
        self._draw_done.clear()

    def draw_frame(self, addr: int):
        assert addr & 0xFFF == 0
        self._map[PAGE_ADDR] = u32(addr >> 12)
        self._map[CTRL] = u32(1)

    async def _handle_irq(self):
        while True:
            self._uio.enable_irq()
            await self._uio.wait_irq()

            irq_status = u32(self._map[IRQ_STATUS])
            self._map[IRQ_STATUS] = u32(irq_status)

            self._draw_done.set()
