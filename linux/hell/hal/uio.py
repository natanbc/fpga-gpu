import asyncio
import mmap
import os
import struct
from pathlib import Path


__all__ = ["Uio"]


class Uio:
    def __init__(self, number: int | str):
        if isinstance(number, str):
            number = Uio._find_number(number)
        self._number = number
        self._fd = os.open(f"/dev/uio{number}", os.O_RDWR)
        self._loop = asyncio.get_event_loop()
        self._loop.add_reader(self._fd, self._read_ready)
        self._irq = asyncio.Event()

    def __del__(self):
        self._loop.remove_reader(self._fd)
        os.close(self._fd)

    def map(self, mapping: int):
        size = int(open(f"/sys/class/uio/uio{self._number}/maps/map{mapping}/size", "r").read().strip(), 16)
        return mmap.mmap(self._fd, size, offset=mapping * 4096)

    def _write(self, v: int):
        res = os.write(self._fd, struct.pack("<I", v))
        if res != 4:
            raise IOError("Failed to change IRQ state")

    def enable_irq(self):
        self._write(1)

    def disable_irq(self):
        self._write(0)

    async def wait_irq(self):
        await self._irq.wait()
        self._irq.clear()

    def _read_ready(self):
        os.read(self._fd, 4)
        self._irq.set()

    @classmethod
    def _find_number(cls, name: str) -> int:
        for p in Path("/sys/class/uio").iterdir():
            uio_name = (p / "name").open("r").read().strip()
            if uio_name == name:
                assert p.name.startswith("uio")
                return int(p.name[3:])
        raise LookupError(f"No UIO device named {name}")
