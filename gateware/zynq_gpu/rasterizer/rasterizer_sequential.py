from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import Component, In, Out, Signature
from .edge_walker import *
from .pixel_writer import *
from ..zynq_ifaces import SAxiHP


PointData = StructLayout({
    "x": unsigned(11),
    "y": unsigned(11),
    "z": unsigned(8),
    "r": unsigned(8),
    "g": unsigned(8),
    "b": unsigned(8),
})


RasterizerData = Signature({
    "valid": Out(1),
    "ready": In(1),
    "points": Out(StructLayout({
        "v0": PointData,
        "v1": PointData,
        "v2": PointData,
    })),
})


class Rasterizer(Component):
    axi: Out(SAxiHP)
    axi2: Out(SAxiHP)
    idle: Out(1)
    width: In(12)
    z_base: In(32)
    fb_base: In(32)

    data: In(RasterizerData)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.axi.aclk.eq(ClockSignal())
        m.d.comb += self.axi2.aclk.eq(ClockSignal())

        m.submodules.walker = walker = EdgeWalker()
        m.submodules.writer = writer = PixelWriter()

        for vertex_idx in range(3):
            walker_vertex = getattr(walker.triangle.payload, f"v{vertex_idx}")
            input_vertex = getattr(self.data.points, f"v{vertex_idx}")
            for sig in ["x", "y"]:
                m.d.comb += getattr(walker_vertex, sig).eq(getattr(input_vertex, sig))

        zs = Array(Signal(8) for _ in range(3))
        rs = Array(Signal(8) for _ in range(3))
        gs = Array(Signal(8) for _ in range(3))
        bs = Array(Signal(8) for _ in range(3))
        ws = Array(Signal(24) for _ in range(3))

        p = Point(Signal(Point))

        z_interp = Signal(8)

        compute_interps = Signal(range(4), reset=3)
        r_interp = Signal(8)
        g_interp = Signal(8)
        b_interp = Signal(8)

        with m.If(compute_interps < 3):
            dest = Array([r_interp, g_interp, b_interp])[compute_interps]
            src = Array([rs, gs, bs])[compute_interps]
            m.d.sync += dest.eq(
                (
                    src[0] * ws[0] +
                    src[1] * ws[1] +
                    src[2] * ws[2] +
                    (1 << 23)
                ) >> 24
            ), compute_interps.eq(compute_interps + 1)

        fetch_z = Signal()
        fetch_z_addr = Signal(32)
        fetch_z_offset = Signal(3)
        fetch_z_read = Signal()
        fetch_z_done = Signal()
        fetched_z = Signal(8)
        with m.If(fetch_z):
            m.d.comb += [
                fetch_z_addr.eq(self.z_base + self.width * p.y + p.x),
                self.axi2.read_address.valid.eq(1),
                self.axi2.read_address.addr.eq(Cat(C(0, 3), fetch_z_addr[3:])),
                self.axi2.read_address.burst.eq(0b01),  # INCR
                self.axi2.read_address.size.eq(0b11),   # 8 bytes/beat
                self.axi2.read_address.len.eq(0),
            ]
            m.d.sync += [
                fetch_z_offset.eq(fetch_z_addr),
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
                    fetched_z.eq(self.axi2.read.data.word_select(fetch_z_offset, 8)),
                    fetch_z_done.eq(1),
                ]

        write_z = Signal()
        write_z_addr = Signal(32)
        write_z_offset = Signal(3)
        write_z_write = Signal()
        write_z_done = Signal()
        m.d.comb += self.axi2.write_response.ready.eq(1)
        with m.If(write_z):
            m.d.comb += [
                write_z_addr.eq(self.z_base + self.width * p.y + p.x),
                self.axi2.write_address.valid.eq(1),
                self.axi2.write_address.addr.eq(Cat(C(0, 3), write_z_addr[3:])),
                self.axi2.write_address.burst.eq(0b01),  # INCR
                self.axi2.write_address.size.eq(0b11),   # 8 bytes/beat
                self.axi2.write_address.len.eq(0),
            ]
            m.d.sync += [
                write_z_offset.eq(write_z_addr),
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
                self.axi2.write_data.data.eq(z_interp.replicate(8)),
                self.axi2.write_data.strb.eq(1 << write_z_offset),
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
                    self.idle.eq(1),
                    self.data.ready.eq(walker.triangle.ready),
                    walker.triangle.valid.eq(self.data.valid),
                ]
                with m.If(self.data.ready & self.data.valid):
                    for vertex_idx in range(3):
                        input_vertex = getattr(self.data.points, f"v{vertex_idx}")
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
                        z_interp.eq(
                            (
                                zs[0] * walker.points.payload.w0 +
                                zs[1] * walker.points.payload.w1 +
                                zs[2] * walker.points.payload.w2 +
                                (1 << 23)
                            ) >> 24
                        ),
                        compute_interps.eq(0),
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
                m.d.comb += writer.pixel_valid.eq(compute_interps == 3)
                with m.If(writer.pixel_valid & writer.pixel_ready):
                    m.next = "DRAW_POINTS"

        return m
