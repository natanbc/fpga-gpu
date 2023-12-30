import struct
from typing import Callable


__all__ = ["u32"]


def u(sz_bit: int, fmt: str) -> Callable[[int | bytes], int | bytes]:
    def f(v: int | bytes) -> bytes | int:
        if isinstance(v, int):
            assert 0 <= v < (1 << sz_bit)
            return struct.pack(fmt, v)
        else:
            assert len(v) == ((sz_bit + 7) // 8)
            return struct.unpack(fmt, v)[0]
    f.__name__ = f"u{sz_bit}"
    return f


u32 = u(32, "<I")
