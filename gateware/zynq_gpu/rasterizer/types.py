from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import In, Out, Signature
from amaranth.utils import log2_int


__all__ = ["Vertex", "TriangleStream", "PerfCounters"]


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
    "points": Out(StructLayout({
        "v0": Vertex,
        "v1": Vertex,
        "v2": Vertex,
    })),
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
