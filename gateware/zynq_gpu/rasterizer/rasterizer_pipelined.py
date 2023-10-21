from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.data import StructLayout, ArrayLayout
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import Component, In, Out, Signature
from .edge_walker import *
from .pixel_writer import *
from ..zynq_ifaces import SAxiHP


PointData = StructLayout({
    "x": unsigned(11),
    "y": unsigned(11),
    "z": unsigned(16),
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


class RasterizerInterpolator(Component):
    width: In(11)

    r: In(ArrayLayout(8, 3))
    g: In(ArrayLayout(8, 3))
    b: In(ArrayLayout(8, 3))
    z: In(ArrayLayout(16, 3))

    idle: Out(1)

    in_ready: Out(1)
    in_valid: In(1)
    in_p: In(Point)
    in_ws: In(ArrayLayout(24, 3))

    out_ready: In(1)
    out_valid: Out(1)
    out_p_offset: Out(23)
    out_r: Out(8)
    out_g: Out(8)
    out_b: Out(8)
    out_z: Out(16)

    def elaborate(self, platform):
        m = Module()

        # Single stall signal so vivado can infer DSP48E pipeline registers
        stall_input = Signal()
        stall_c3 = Signal()

        c0_r = Signal.like(self.r, reset_less=True)
        c0_g = Signal.like(self.g, reset_less=True)
        c0_b = Signal.like(self.b, reset_less=True)
        c0_z = Signal.like(self.z, reset_less=True)
        c0_p = Signal.like(self.in_p, reset_less=True)
        c0_ws = Signal.like(self.in_ws, reset_less=True)
        c0_valid = Signal()

        with m.If(~stall_input):
            m.d.sync += [
                c0_p.eq(self.in_p),
                c0_ws.eq(self.in_ws),
                c0_r.eq(self.r),
                c0_g.eq(self.g),
                c0_b.eq(self.b),
                c0_z.eq(self.z),
                c0_valid.eq(self.in_valid),
            ]

        c1_r = Signal.like(self.r, reset_less=True)
        c1_g = Signal.like(self.g, reset_less=True)
        c1_b = Signal.like(self.b, reset_less=True)
        c1_z = Signal.like(self.z, reset_less=True)
        c1_p = Signal.like(self.in_p, reset_less=True)
        c1_ws = Signal.like(self.in_ws, reset_less=True)
        c1_valid = Signal()

        with m.If(~stall_input):
            m.d.sync += [
                c1_p.eq(c0_p),
                c1_ws.eq(c0_ws),
                c1_r.eq(c0_r),
                c1_g.eq(c0_g),
                c1_b.eq(c0_b),
                c1_z.eq(c0_z),
                c1_valid.eq(c0_valid),
            ]

        c2_r_scaled = Array(Signal(32, name=f"r_scaled_{i}", reset_less=True) for i in range(3))
        c2_g_scaled = Array(Signal(32, name=f"g_scaled_{i}", reset_less=True) for i in range(3))
        c2_b_scaled = Array(Signal(32, name=f"b_scaled_{i}", reset_less=True) for i in range(3))
        c2_z_scaled = Array(Signal(40, name=f"z_scaled_{i}", reset_less=True) for i in range(3))
        c2_p_offset = Signal(23, reset_less=True)
        c2_valid = Signal()

        width_s = Signal(signed(12))
        p_x_s = Signal(signed(12))
        p_y_s = Signal(signed(12))

        m.d.comb += [
            width_s.eq(Cat(C(0, 1), self.width)),
            p_x_s.eq(Cat(C(0, 1), self.in_p.x)),
            p_y_s.eq(Cat(C(0, 1), self.in_p.y)),
        ]

        with m.If(~stall_input):
            for in_, out in zip(
                [c1_r,        c1_g,        c1_b,        c1_z],
                [c2_r_scaled, c2_g_scaled, c2_b_scaled, c2_z_scaled],
            ):
                for i in range(3):
                    m.d.sync += out[i].eq(in_[i] * c1_ws[i])
            m.d.sync += [
                c2_p_offset.eq(self.width * c1_p.y + c1_p.x),
                c2_valid.eq(c1_valid),
            ]

        c3_r = Signal(8, reset_less=True)
        c3_g = Signal(8, reset_less=True)
        c3_b = Signal(8, reset_less=True)
        c3_z = Signal(16, reset_less=True)
        c3_p_offset = Signal(23, reset_less=True)
        c3_valid = Signal()

        with m.If(~stall_c3):
            for in_, out in zip(
                [c2_r_scaled, c2_g_scaled, c2_b_scaled, c2_z_scaled],
                [c3_r,        c3_g,        c3_b,        c3_z],
            ):
                m.d.sync += out.eq(
                    (sum(in_) + (1 << 23)) >> 24
                )
            m.d.sync += [
                c3_p_offset.eq(c2_p_offset),
                c3_valid.eq(c2_valid),
            ]

        m.d.comb += [
            self.idle.eq(~c0_valid & ~c1_valid & ~c2_valid & ~c3_valid),
            self.in_ready.eq(~stall_input),

            stall_input.eq((c0_valid | c1_valid | c2_valid) & stall_c3),
            stall_c3.eq(c3_valid & ~self.out_ready),

            self.out_valid.eq(c3_valid),
            self.out_p_offset.eq(c3_p_offset),
            self.out_r.eq(c3_r),
            self.out_g.eq(c3_g),
            self.out_b.eq(c3_b),
            self.out_z.eq(c3_z),
        ]

        return m


class RasterizerDepthTester(Component):
    idle: Out(1)

    in_ready: Out(1)
    in_valid: In(1)
    in_p_offset: In(23)
    in_r: In(8)
    in_g: In(8)
    in_b: In(8)
    in_z: In(16)

    out_ready: In(1)
    out_valid: Out(1)
    out_p_offset: Out(23)
    out_r: Out(8)
    out_g: Out(8)
    out_b: Out(8)
    out_z: Out(16)
    
    zst_ready: Out(1)
    zst_valid: In(1)
    zst_word: In(64)

    def elaborate(self, platform):
        m = Module()

        stall = Signal()

        p_offset = Signal(23, reset_less=True)
        r = Signal(8, reset_less=True)
        g = Signal(8, reset_less=True)
        b = Signal(8, reset_less=True)
        z = Signal(16, reset_less=True)
        fetched_z = Signal(16, reset_less=True)
        valid_data = Signal()

        with m.If(~stall):
            m.d.comb += [
                self.zst_ready.eq(self.in_valid),
            ]
            m.d.sync += [
                p_offset.eq(self.in_p_offset),
                r.eq(self.in_r),
                g.eq(self.in_g),
                b.eq(self.in_b),
                z.eq(self.in_z),
                fetched_z.eq(self.zst_word.word_select(self.in_p_offset[:2], 16)),
                valid_data.eq(self.zst_ready & self.zst_valid),
            ]

        valid = Signal()
        m.d.comb += [
            valid.eq(valid_data & (fetched_z < z))
        ]

        m.d.comb += [
            self.idle.eq(~valid),

            self.in_ready.eq(~stall & self.zst_valid),
            stall.eq(valid & ~self.out_ready),

            self.out_valid.eq(valid),

            self.out_p_offset.eq(p_offset),
            self.out_r.eq(r),
            self.out_g.eq(g),
            self.out_b.eq(b),
            self.out_z.eq(z),
        ]

        return m


class RasterizerWriter(Component):
    fb_base: In(32)
    width: In(11)

    idle: Out(1)

    ready: Out(1)
    valid: In(1)
    p_offset: In(23)
    r: In(8)
    g: In(8)
    b: In(8)

    axi: Out(SAxiHP)

    def elaborate(self, platform):
        m = Module()

        m.submodules.writer = writer = PixelWriter()
        m.d.comb += self.axi.aclk.eq(ClockSignal())
        wiring.connect(m, wiring.flipped(self.axi.write_address), writer.axi_addr)
        wiring.connect(m, wiring.flipped(self.axi.write_data), writer.axi_data)
        wiring.connect(m, wiring.flipped(self.axi.write_response), writer.axi_resp)

        s0_stall = Signal()

        s0_addr = Signal(32, reset_less=True)
        s0_r = Signal(8, reset_less=True)
        s0_g = Signal(8, reset_less=True)
        s0_b = Signal(8, reset_less=True)
        s0_valid = Signal()

        with m.If(~s0_stall):
            m.d.sync += [
                s0_addr.eq(self.fb_base + self.p_offset * 3),
                s0_r.eq(self.r),
                s0_g.eq(self.g),
                s0_b.eq(self.b),
                s0_valid.eq(self.valid),
            ]

        m.d.comb += [
            writer.pixel_data.eq(Cat(s0_b, s0_g, s0_r)),
            writer.pixel_addr.eq(s0_addr),
            writer.pixel_valid.eq(s0_valid),
        ]

        m.d.comb += [
            self.idle.eq(~s0_valid & writer.pixel_ready),
            self.ready.eq(~s0_stall),
            s0_stall.eq(s0_valid & ~writer.pixel_ready),
        ]

        return m


class Rasterizer(Component):
    axi: Out(SAxiHP)
    axi2: Out(SAxiHP)
    idle: Out(1)
    width: In(12)
    z_base: In(32)
    fb_base: In(32)

    perf_counters: Out(StructLayout({
        "stalls": StructLayout({
            "walker": 32,
            "depth_load_addr": 32,
            "depth_fifo": 32,
            "depth_store_addr": 32,
            "depth_store_data": 32,
            "pixel_store": 32,
        }),
        "depth_fifo_buckets": ArrayLayout(32, 8),
    }))

    data: In(RasterizerData)

    def elaborate(self, platform):
        m = Module()

        stall_walker = Signal()
        stall_depth_load_addr = Signal()
        stall_depth_fifo = Signal()
        stall_depth_store_addr = Signal()
        stall_depth_store_data = Signal()
        stall_pixel_store = Signal()
        with m.If(stall_walker):
            m.d.sync += self.perf_counters.stalls.walker.eq(self.perf_counters.stalls.walker + 1)
        with m.If(stall_depth_load_addr):
            m.d.sync += self.perf_counters.stalls.depth_load_addr.eq(self.perf_counters.stalls.depth_load_addr + 1)
        with m.If(stall_depth_fifo):
            m.d.sync += self.perf_counters.stalls.depth_fifo.eq(self.perf_counters.stalls.depth_fifo + 1)
        with m.If(stall_depth_store_addr):
            m.d.sync += self.perf_counters.stalls.depth_store_addr.eq(self.perf_counters.stalls.depth_store_addr + 1)
        with m.If(stall_depth_store_data):
            m.d.sync += self.perf_counters.stalls.depth_store_data.eq(self.perf_counters.stalls.depth_store_data + 1)
        with m.If(stall_pixel_store):
            m.d.sync += self.perf_counters.stalls.pixel_store.eq(self.perf_counters.stalls.pixel_store + 1)

        m.submodules.walker = walker = EdgeWalker()
        m.submodules.interpolator = interpolator = RasterizerInterpolator()
        m.submodules.depth_tester = depth_tester = RasterizerDepthTester()
        m.submodules.writer = writer = RasterizerWriter()

        m.d.comb += self.axi2.aclk.eq(ClockSignal())

        fifo_empty = Signal(reset=1)
        m.d.comb += [
            interpolator.width.eq(self.width),
            writer.fb_base.eq(self.fb_base),
            writer.width.eq(self.width),

            depth_tester.zst_valid.eq(self.axi2.read.valid),
            self.axi2.read.ready.eq(depth_tester.zst_ready),
            depth_tester.zst_word.eq(self.axi2.read.data),

            self.idle.eq(walker.idle & interpolator.idle & fifo_empty & depth_tester.idle & writer.idle),
        ]
        wiring.connect(m, wiring.flipped(self.axi), writer.axi)

        for vertex_idx in range(3):
            walker_vertex = getattr(walker.triangle.payload, f"v{vertex_idx}")
            input_vertex = getattr(self.data.points, f"v{vertex_idx}")
            for sig in ["x", "y"]:
                m.d.comb += getattr(walker_vertex, sig).eq(getattr(input_vertex, sig))

        m.d.comb += [
            self.data.ready.eq(walker.triangle.ready),
            walker.triangle.valid.eq(self.data.valid),
        ]
        with m.If(self.data.ready & self.data.valid):
            for vertex_idx in range(3):
                input_vertex = getattr(self.data.points, f"v{vertex_idx}")
                for sig in "rgbz":
                    m.d.sync += getattr(interpolator, sig)[vertex_idx].eq(getattr(input_vertex, sig))

        m.d.sync += stall_walker.eq(walker.points.valid & ~walker.points.ready)
        m.d.comb += [
            walker.points.ready.eq(interpolator.in_ready),
            interpolator.in_valid.eq(walker.points.valid),

            interpolator.in_p.eq(walker.points.payload.p),
            interpolator.in_ws[0].eq(walker.points.payload.w0),
            interpolator.in_ws[1].eq(walker.points.payload.w1),
            interpolator.in_ws[2].eq(walker.points.payload.w2),
        ]

        m.submodules.fifo = fifo = SyncFIFOBuffered(width=23 + 3 * 8 + 16, depth=32)
        m.d.comb += fifo_empty.eq(~fifo.r_rdy)

        m.d.sync += [
            self.perf_counters.depth_fifo_buckets[fifo.level[2:]].eq(
                self.perf_counters.depth_fifo_buckets[fifo.level[2:]] + 1
            ),
        ]

        accept_interp = Signal()

        m.d.sync += [
            stall_depth_load_addr.eq(interpolator.out_valid & ~self.axi2.read_address.ready),
            stall_depth_fifo.eq(interpolator.out_valid & ~fifo.r_rdy),
        ]
        m.d.comb += [
            accept_interp.eq(fifo.w_rdy & self.axi2.read_address.ready),
            self.axi2.read_address.addr.eq(
                Cat(C(0, 3), (self.z_base + interpolator.out_p_offset*2)[3:]),
            ),
            self.axi2.read_address.burst.eq(0b01),    # INCR
            self.axi2.read_address.size.eq(0b11),     # 8 bytes/beat
            self.axi2.read_address.len.eq(0),
            self.axi2.read_address.cache.eq(0b1111),

            interpolator.out_ready.eq(accept_interp),


            fifo.w_en.eq(interpolator.out_valid & self.axi2.read_address.ready),
            self.axi2.read_address.valid.eq(interpolator.out_valid & fifo.w_rdy),

            fifo.w_data.eq(Cat(
                interpolator.out_b,
                interpolator.out_g,
                interpolator.out_r,
                interpolator.out_z,
                interpolator.out_p_offset,
            ))
        ]

        m.d.comb += [
            fifo.r_en.eq(depth_tester.in_ready),
            depth_tester.in_valid.eq(fifo.r_rdy),
            Cat(
                depth_tester.in_b,
                depth_tester.in_g,
                depth_tester.in_r,
                depth_tester.in_z,
                depth_tester.in_p_offset,
            ).eq(fifo.r_data),
        ]

        accept_pix = Signal()

        m.d.sync += [
            stall_depth_store_addr.eq(depth_tester.out_valid & ~self.axi2.write_address.ready),
            stall_depth_store_data.eq(depth_tester.out_valid & ~self.axi2.write_data.ready),
            stall_pixel_store.eq(depth_tester.out_valid & ~writer.ready),
        ]
        m.d.comb += [
            self.axi2.write_address.addr.eq(Cat(C(0, 3), (self.z_base + depth_tester.out_p_offset*2)[3:])),
            self.axi2.write_address.burst.eq(0b01),    # INCR
            self.axi2.write_address.size.eq(0b11),     # 8 bytes/beat
            self.axi2.write_address.len.eq(0),
            self.axi2.write_address.cache.eq(0b1111),

            self.axi2.write_data.data.eq(depth_tester.out_z.replicate(4)),
            self.axi2.write_data.strb.eq(0b11 << (depth_tester.out_p_offset[:2] * 2)),
            self.axi2.write_data.last.eq(1),

            self.axi2.write_response.ready.eq(1),

            accept_pix.eq(writer.ready & self.axi2.write_data.ready & self.axi2.write_address.ready),

            self.axi2.write_address.valid.eq(depth_tester.out_valid & writer.ready & self.axi2.write_data.ready),
            self.axi2.write_data.valid.eq(depth_tester.out_valid & writer.ready & self.axi2.write_address.ready),

            depth_tester.out_ready.eq(accept_pix),
            writer.valid.eq(depth_tester.out_valid & self.axi2.write_data.ready & self.axi2.write_address.ready),

            writer.p_offset.eq(depth_tester.out_p_offset),
            writer.r.eq(depth_tester.out_r),
            writer.g.eq(depth_tester.out_g),
            writer.b.eq(depth_tester.out_b),
        ]

        return m
