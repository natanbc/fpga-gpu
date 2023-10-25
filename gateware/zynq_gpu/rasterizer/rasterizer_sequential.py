from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import Component, In, Out
from .edge_walker import *
from .pixel_writer import *
from .types import *
from ..zynq_ifaces import SAxiHP


__all__ = ["Rasterizer"]


class Rasterizer(Component):
    axi: Out(SAxiHP)
    axi2: Out(SAxiHP)
    idle: Out(1)
    width: In(12)
    z_base: In(32)
    fb_base: In(32)

    # Unused, but keeps signature compatible with pipelined
    perf_counters: Out(PerfCounters)

    command_idle: In(1)

    triangles: In(TriangleStream)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.axi.aclk.eq(ClockSignal())
        m.d.comb += self.axi2.aclk.eq(ClockSignal())

        m.submodules.walker = walker = EdgeWalker()
        m.submodules.writer = writer = PixelWriter()

        for vertex_idx in range(3):
            walker_vertex = getattr(walker.triangle.payload, f"v{vertex_idx}")
            input_vertex = getattr(self.triangles.payload, f"v{vertex_idx}")
            for sig in ["x", "y"]:
                m.d.comb += getattr(walker_vertex, sig).eq(getattr(input_vertex, sig))

        rs = Array(Signal(8) for _ in range(3))
        gs = Array(Signal(8) for _ in range(3))
        bs = Array(Signal(8) for _ in range(3))
        zs = Array(Signal(16) for _ in range(3))
        ws = Array(Signal(24) for _ in range(3))

        p = Point(Signal(Point))

        r_interp = Signal(8)
        g_interp = Signal(8)
        b_interp = Signal(8)
        z_interp = Signal(16)

        for dest, src in zip([r_interp, g_interp, b_interp, z_interp], [rs, gs, bs, zs]):
            m.d.sync += dest.eq(
                (
                    src[0] * ws[0] +
                    src[1] * ws[1] +
                    src[2] * ws[2] +
                    (1 << 23)
                ) >> 24
            )

        fetch_z = Signal()
        fetch_z_offset = Signal(2)
        fetch_z_read = Signal()
        fetch_z_done = Signal()
        fetched_z = Signal(16)
        with m.If(fetch_z):
            fetch_z_xy = Signal(32)
            m.d.comb += [
                fetch_z_xy.eq(self.width * p.y + p.x),
                self.axi2.read_address.valid.eq(1),
                self.axi2.read_address.addr.eq(Cat(C(0, 3), (self.z_base + fetch_z_xy * 2)[3:])),
                self.axi2.read_address.burst.eq(0b01),  # INCR
                self.axi2.read_address.size.eq(0b11),   # 8 bytes/beat
                self.axi2.read_address.len.eq(0),
            ]
            m.d.sync += [
                fetch_z_offset.eq(fetch_z_xy[:2]),
            ]
            with m.If(self.axi2.read_address.ready):
                m.d.sync += [
                    fetch_z.eq(0),
                    fetch_z_read.eq(1),
                ]
        with m.If(fetch_z_read):
            m.d.comb += self.axi2.read.ready.eq(1)
            with m.If(self.axi2.read.valid):
                m.d.sync += fetch_z_read.eq(0)
                m.d.comb += [
                    fetched_z.eq(self.axi2.read.data.word_select(fetch_z_offset, 16)),
                    fetch_z_done.eq(1),
                ]

        write_z = Signal()
        write_z_offset = Signal(2)
        write_z_write = Signal()
        write_z_done = Signal()
        m.d.comb += self.axi2.write_response.ready.eq(1)
        with m.If(write_z):
            write_z_xy = Signal(32)
            m.d.comb += [
                write_z_xy.eq(self.width * p.y + p.x),
                self.axi2.write_address.valid.eq(1),
                self.axi2.write_address.addr.eq(Cat(C(0, 3), (self.z_base + write_z_xy * 2)[3:])),
                self.axi2.write_address.burst.eq(0b01),  # INCR
                self.axi2.write_address.size.eq(0b11),   # 8 bytes/beat
                self.axi2.write_address.len.eq(0),
            ]
            m.d.sync += [
                write_z_offset.eq(write_z_xy[:2]),
            ]
            with m.If(self.axi2.write_address.ready):
                m.d.sync += [
                    write_z.eq(0),
                    write_z_write.eq(1),
                ]
        with m.If(write_z_write):
            m.d.comb += [
                self.axi2.write_data.valid.eq(1),
                self.axi2.write_data.last.eq(1),
                self.axi2.write_data.data.eq(z_interp.replicate(4)),
                self.axi2.write_data.strb.eq(0b11 << (write_z_offset * 2)),
            ]

            with m.If(self.axi2.write_data.ready):
                m.d.sync += write_z_write.eq(0)
                m.d.comb += write_z_done.eq(1)

        m.d.comb += [
            writer.pixel_data.eq(Cat(b_interp, g_interp, r_interp)),
            writer.pixel_addr.eq(self.fb_base + (self.width * p.y + p.x) * 3),
        ]
        wiring.connect(m, wiring.flipped(self.axi.write_address), writer.axi_addr)
        wiring.connect(m, wiring.flipped(self.axi.write_data), writer.axi_data)
        wiring.connect(m, wiring.flipped(self.axi.write_response), writer.axi_resp)

        with m.FSM():
            with m.State("IDLE"):
                m.d.comb += [
                    self.idle.eq(self.command_idle),
                    self.triangles.ready.eq(walker.triangle.ready),
                    walker.triangle.valid.eq(self.triangles.valid),
                ]
                with m.If(self.triangles.ready & self.triangles.valid):
                    for vertex_idx in range(3):
                        input_vertex = getattr(self.triangles.payload, f"v{vertex_idx}")
                        for sig, arr in [("z", zs), ("r", rs), ("g", gs), ("b", bs)]:
                            m.d.sync += arr[vertex_idx].eq(getattr(input_vertex, sig))
                    m.next = "DRAW_POINTS"
            with m.State("DRAW_POINTS"):
                m.d.comb += walker.points.ready.eq(1)
                with m.If(walker.points.valid):
                    for idx in range(3):
                        m.d.sync += ws[idx].eq(getattr(walker.points.payload, f"w{idx}"))
                    m.d.sync += [
                        p.eq(walker.points.payload.p),
                        fetch_z.eq(1),
                    ]
                    m.next = "CHECK_Z"
                with m.Elif(walker.idle):
                    m.next = "IDLE"
            with m.State("CHECK_Z"):
                with m.If(fetch_z_done):
                    with m.If(z_interp > fetched_z):
                        m.d.sync += write_z.eq(1)
                        m.next = "WRITE_Z"
                    with m.Else():
                        m.next = "DRAW_POINTS"
            with m.State("WRITE_Z"):
                with m.If(write_z_done):
                    m.next = "WRITE_PIXEL"
            with m.State("WRITE_PIXEL"):
                m.d.comb += writer.pixel_valid.eq(1)
                with m.If(writer.pixel_valid & writer.pixel_ready):
                    m.next = "DRAW_POINTS"

        return m
