from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import In, Out, Signature
from amaranth.utils import log2_int


__all__ = ["Vertex", "TriangleStream", "TextureBufferRead", "TextureBufferWrite", "BufferClearStream", "PerfCounters"]


Vertex = StructLayout({
    "x": unsigned(11),
    "y": unsigned(11),
    "z": unsigned(16),
    "r": unsigned(8),
    "g": unsigned(8),
    "b": unsigned(8),
})


TriangleStream = Signature({
    "valid": Out(1),
    "ready": In(1),
    "payload": Out(StructLayout({
        "v0": Vertex,
        "v1": Vertex,
        "v2": Vertex,
        "texture_buffer": 2,
        "texture_enable": 1,
    })),
})


TextureBufferRead = Signature({
    "en": Out(1),
    "buffer": Out(2),
    "s": Out(7),
    "t": Out(7),
    # Available on the next cycle after s/t/buffer are set
    "color": In(24),
})


TextureBufferWrite = Signature({
    "en": Out(1),
    "buffer": Out(2),
    "addr": Out(13),
    "data": Out(48),
})


BufferClearStream = Signature({
    "ready": In(1),
    "valid": Out(1),

    "payload": Out(StructLayout({
        "base_addr": 25,   # 128-byte aligned base address. The 7 LSBs are filled with zeroes.
        "words": 20,       # How many words of data should be written.
        "pattern": 24,     # Works for both depth and frame buffers
        "qos": 4,          # AXI QOS field.
    }))
})


# Each signal is a strobe to increment, depth_fifo_bucket is the index of which bucket to increment
PerfCounters = StructLayout({
    "busy": 1,
    "stalls": StructLayout({
        "walker_searching": 1,
        "walker": 1,
        "depth_load_addr": 1,
        "depth_fifo": 1,
        "depth_store_addr": 1,
        "depth_store_data": 1,
        "pixel_store": 1,
    }),
    "depth_fifo_bucket": log2_int(9, need_pow2=False),
})
