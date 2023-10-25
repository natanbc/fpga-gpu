from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.data import ArrayLayout
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import Component, In, Out
from .edge_walker import *
from .pixel_writer import *
from .types import *
from ..zynq_ifaces import SAxiHP


__all__ = ["Rasterizer"]


class RasterizerInterpolator(Component):
    width: In(11)

    r: In(ArrayLayout(8, 3))
    g: In(ArrayLayout(8, 3))
    b: In(ArrayLayout(8, 3))
    z: In(ArrayLayout(16, 3))
    texture_buffer: In(2)
    texture_enable: In(1)

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
    out_texture_buffer: Out(2)
    out_texture_enable: Out(1)

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
        c0_texture_buffer = Signal.like(self.texture_buffer)
        c0_texture_enable = Signal.like(self.texture_enable)
        c0_valid = Signal()

        with m.If(~stall_input):
            m.d.sync += [
                c0_p.eq(self.in_p),
                c0_ws.eq(self.in_ws),
                c0_r.eq(self.r),
                c0_g.eq(self.g),
                c0_b.eq(self.b),
                c0_z.eq(self.z),
                c0_texture_buffer.eq(self.texture_buffer),
                c0_texture_enable.eq(self.texture_enable),
                c0_valid.eq(self.in_valid),
            ]

        c1_r = Signal.like(self.r, reset_less=True)
        c1_g = Signal.like(self.g, reset_less=True)
        c1_b = Signal.like(self.b, reset_less=True)
        c1_z = Signal.like(self.z, reset_less=True)
        c1_p = Signal.like(self.in_p, reset_less=True)
        c1_ws = Signal.like(self.in_ws, reset_less=True)
        c1_texture_buffer = Signal.like(self.texture_buffer)
        c1_texture_enable = Signal.like(self.texture_enable)
        c1_valid = Signal()

        with m.If(~stall_input):
            m.d.sync += [
                c1_p.eq(c0_p),
                c1_ws.eq(c0_ws),
                c1_r.eq(c0_r),
                c1_g.eq(c0_g),
                c1_b.eq(c0_b),
                c1_z.eq(c0_z),
                c1_texture_buffer.eq(c0_texture_buffer),
                c1_texture_enable.eq(c0_texture_enable),
                c1_valid.eq(c0_valid),
            ]

        c2_r_scaled = Array(Signal(32, name=f"r_scaled_{i}", reset_less=True) for i in range(3))
        c2_g_scaled = Array(Signal(32, name=f"g_scaled_{i}", reset_less=True) for i in range(3))
        c2_b_scaled = Array(Signal(32, name=f"b_scaled_{i}", reset_less=True) for i in range(3))
        c2_z_scaled = Array(Signal(40, name=f"z_scaled_{i}", reset_less=True) for i in range(3))
        c2_p_offset = Signal(23, reset_less=True)
        c2_texture_buffer = Signal.like(self.texture_buffer)
        c2_texture_enable = Signal.like(self.texture_enable)
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
                c2_texture_buffer.eq(c1_texture_buffer),
                c2_texture_enable.eq(c1_texture_enable),
                c2_valid.eq(c1_valid),
            ]

        c3_r = Signal(9, reset_less=True)
        c3_g = Signal(9, reset_less=True)
        c3_b = Signal(9, reset_less=True)
        c3_z = Signal(17, reset_less=True)
        c3_p_offset = Signal(23, reset_less=True)
        c3_texture_buffer = Signal.like(self.texture_buffer)
        c3_texture_enable = Signal.like(self.texture_enable)
        c3_valid = Signal()

        with m.If(~stall_c3):
            for in_, out in zip(
                    [c2_r_scaled, c2_g_scaled, c2_b_scaled, c2_z_scaled],
                    [c3_r,        c3_g,        c3_b,        c3_z],
            ):
                m.d.sync += out.eq(sum(in_) >> 23)
            m.d.sync += [
                c3_p_offset.eq(c2_p_offset),
                c3_texture_buffer.eq(c2_texture_buffer),
                c3_texture_enable.eq(c2_texture_enable),
                c3_valid.eq(c2_valid),
            ]

        m.d.comb += [
            self.idle.eq(~c0_valid & ~c1_valid & ~c2_valid & ~c3_valid),
            self.in_ready.eq(~stall_input),

            stall_input.eq((c0_valid | c1_valid | c2_valid) & stall_c3),
            stall_c3.eq(c3_valid & ~self.out_ready),

            self.out_valid.eq(c3_valid),
            self.out_p_offset.eq(c3_p_offset),
            self.out_r.eq((c3_r + 1) >> 1),
            self.out_g.eq((c3_g + 1) >> 1),
            self.out_b.eq((c3_b + 1) >> 1),
            self.out_z.eq((c3_z + 1) >> 1),
            self.out_texture_buffer.eq(c3_texture_buffer),
            self.out_texture_enable.eq(c3_texture_enable),
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
    in_texture_buffer: In(2)
    in_texture_enable: In(1)

    out_ready: In(1)
    out_valid: Out(1)
    out_p_offset: Out(23)
    out_r: Out(8)
    out_g: Out(8)
    out_b: Out(8)
    out_z: Out(16)
    out_texture_buffer: Out(2)
    out_texture_enable: Out(1)

    zst_ready: Out(1)
    zst_valid: In(1)
    zst_z: In(16)

    def elaborate(self, platform):
        m = Module()

        stall = Signal()

        p_offset = Signal(23, reset_less=True)
        r = Signal(8, reset_less=True)
        g = Signal(8, reset_less=True)
        b = Signal(8, reset_less=True)
        z = Signal(16, reset_less=True)
        fetched_z = Signal(16, reset_less=True)
        texture_buffer = Signal(2)
        texture_enable = Signal()
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
                fetched_z.eq(self.zst_z),
                texture_buffer.eq(self.in_texture_buffer),
                texture_enable.eq(self.in_texture_enable),
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
            self.out_texture_buffer.eq(texture_buffer),
            self.out_texture_enable.eq(texture_enable),
        ]

        return m


class ZReader(Component):
    idle: Out(1)

    read_address: Out(SAxiHP.members["read_address"].signature)
    read: Out(SAxiHP.members["read"].signature)

    in_addr_ready: Out(1)
    in_addr_valid: In(1)
    in_addr: In(32)

    out_z_ready: In(1)
    out_z_valid: Out(1)
    out_z: Out(16)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [
            self.read_address.burst.eq(0b01),    # INCR
            self.read_address.size.eq(0b11),     # 8 bytes/beat
            self.read_address.len.eq(0),
            self.read_address.cache.eq(0b1111),
        ]

        stall_c0 = Signal()
        stall_c1 = Signal()
        stall_c2 = Signal()

        last_addr = Signal(32, reset=1)

        in_addr_word = Signal(32)
        m.d.comb += in_addr_word.eq(Cat(C(0, 3), self.in_addr[3:]))

        c0_addr = Signal(32)
        c0_offset = Signal(2)
        c0_valid = Signal()

        with m.If(~stall_c0):
            m.d.sync += [
                c0_addr.eq(in_addr_word),
                c0_offset.eq(self.in_addr[1:3]),
                c0_valid.eq(self.in_addr_valid),
            ]

        c1_addr = Signal(32)
        c1_offset = Signal(2)
        c1_same_addr = Signal()
        c1_valid = Signal()

        with m.If(~stall_c1):
            m.d.sync += [
                c1_addr.eq(c0_addr),
                c1_offset.eq(c0_offset),
                c1_same_addr.eq(c0_addr == last_addr),
                c1_valid.eq(c0_valid),
            ]
            with m.If(c0_valid):
                m.d.sync += last_addr.eq(c0_addr)

        # TODO: replace this dirty hack with a proper skid buffer
        fifo_input = Cat(c1_addr, c1_offset, c1_same_addr)
        m.submodules.c1c2_fifo = c1c2_fifo = SyncFIFOBuffered(width=len(fifo_input), depth=2)

        c1c2_addr = Signal(32)
        c1c2_offset = Signal(2)
        c1c2_same_addr = Signal()
        c1c2_valid = Signal()

        m.d.comb += [
            c1c2_fifo.w_data.eq(fifo_input),
            c1c2_fifo.w_en.eq(c1_valid),

            Cat(c1c2_addr, c1c2_offset, c1c2_same_addr).eq(c1c2_fifo.r_data),
            c1c2_fifo.r_en.eq(~stall_c2),
            c1c2_valid.eq(c1c2_fifo.r_rdy),
        ]

        m.submodules.load_queue = load_queue = SyncFIFOBuffered(width=3, depth=64)

        with m.If(~stall_c2):
            m.d.comb += [
                self.read_address.valid.eq(c1c2_valid & ~c1c2_same_addr),
                self.read_address.addr.eq(c1c2_addr),
                load_queue.w_en.eq(c1c2_valid),
                load_queue.w_data.eq(Cat(c1c2_same_addr, c1c2_offset)),
            ]

        m.d.comb += [
            self.idle.eq(~c0_valid & ~c1_valid & ~load_queue.r_rdy),
            self.in_addr_ready.eq(~stall_c0),
            stall_c0.eq(c0_valid & stall_c1),
            stall_c1.eq(c1_valid & ~c1c2_fifo.w_rdy),
            stall_c2.eq(~load_queue.w_rdy | ~self.read_address.ready),
        ]

        # ================================

        last_word = Signal(64)

        load_same_addr = Signal()
        load_offset = Signal(2)
        m.d.comb += [
            Cat(load_same_addr, load_offset).eq(load_queue.r_data),

            self.read.ready.eq((load_queue.r_rdy & ~load_same_addr) & self.out_z_ready),
            load_queue.r_en.eq(self.out_z_ready & self.out_z_valid),

            self.out_z.eq(Mux(
                load_same_addr,
                last_word.word_select(load_offset, 16),
                self.read.data.word_select(load_offset, 16),
            )),
            self.out_z_valid.eq(self.read.valid | (load_queue.r_rdy & load_same_addr)),
        ]
        with m.If(self.read.ready & self.read.valid):
            m.d.sync += last_word.eq(self.read.data)

        return m


class RasterizerTextureMapper(Component):
    in_ready: Out(1)
    in_valid: In(1)
    in_p_offset: In(23)
    in_r: In(8)
    in_g: In(8)
    in_b: In(8)
    in_texture_buffer: In(2)
    in_texture_enable: In(1)

    out_ready: In(1)
    out_valid: Out(1)
    out_p_offset: Out(23)
    out_r: Out(8)
    out_g: Out(8)
    out_b: Out(8)

    def elaborate(self, platform):
        m = Module()

        stall = Signal()

        p_offset = Signal(23)
        r = Signal(8)
        g = Signal(8)
        b = Signal(8)
        valid = Signal()

        with m.If(~stall):
            m.d.sync += [
                p_offset.eq(self.in_p_offset),
                r.eq(self.in_r),
                g.eq(self.in_g),
                b.eq(self.in_b),
                valid.eq(self.in_valid),
            ]

        m.d.comb += [
            self.in_ready.eq(~stall),
            stall.eq(valid & ~self.out_ready),

            self.out_valid.eq(valid),
            self.out_p_offset.eq(p_offset),
            self.out_r.eq(r),
            self.out_g.eq(g),
            self.out_b.eq(b),
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

    perf_counters: Out(PerfCounters)

    command_idle: In(1)

    triangles: In(TriangleStream)

    def elaborate(self, platform):
        m = Module()

        m.submodules.walker = walker = EdgeWalker()
        m.submodules.interpolator = interpolator = RasterizerInterpolator()
        m.submodules.z_reader = z_reader = ZReader()
        m.submodules.depth_tester = depth_tester = RasterizerDepthTester()
        m.submodules.texture_mapper = texture_mapper = RasterizerTextureMapper()
        m.submodules.writer = writer = RasterizerWriter()

        m.d.sync += self.perf_counters.busy.eq(~self.idle)
        m.d.comb += self.axi2.aclk.eq(ClockSignal())

        fifo_empty = Signal(reset=1)
        m.d.comb += [
            interpolator.width.eq(self.width),
            writer.fb_base.eq(self.fb_base),
            writer.width.eq(self.width),
        ]
        wiring.connect(m, wiring.flipped(self.axi), writer.axi)
        wiring.connect(m, wiring.flipped(self.axi2.read_address), z_reader.read_address)
        wiring.connect(m, wiring.flipped(self.axi2.read), z_reader.read)

        idle0 = Signal()
        m.d.sync += idle0.eq(self.command_idle & walker.idle & interpolator.idle & fifo_empty)
        idle1 = Signal()
        m.d.sync += idle1.eq(z_reader.idle & depth_tester.idle & writer.idle)
        m.d.sync += self.idle.eq(idle0 & idle1)

        for vertex_idx in range(3):
            walker_vertex = getattr(walker.triangle.payload, f"v{vertex_idx}")
            input_vertex = getattr(self.triangles.payload, f"v{vertex_idx}")
            for sig in ["x", "y"]:
                m.d.comb += getattr(walker_vertex, sig).eq(getattr(input_vertex, sig))

        m.d.comb += [
            self.triangles.ready.eq(walker.triangle.ready),
            walker.triangle.valid.eq(self.triangles.valid),
        ]
        with m.If(self.triangles.ready & self.triangles.valid):
            for vertex_idx in range(3):
                input_vertex = getattr(self.triangles.payload, f"v{vertex_idx}")
                for sig in "rgbz":
                    m.d.sync += getattr(interpolator, sig)[vertex_idx].eq(getattr(input_vertex, sig))

        m.d.sync += [
            self.perf_counters.stalls.walker_searching.eq(~walker.idle & ~walker.points.valid & walker.points.ready),
            self.perf_counters.stalls.walker.eq(walker.points.valid & ~walker.points.ready),
        ]
        m.d.comb += [
            walker.points.ready.eq(interpolator.in_ready),
            interpolator.in_valid.eq(walker.points.valid),

            interpolator.in_p.eq(walker.points.payload.p),
            interpolator.in_ws[0].eq(walker.points.payload.w0),
            interpolator.in_ws[1].eq(walker.points.payload.w1),
            interpolator.in_ws[2].eq(walker.points.payload.w2),
        ]

        m.submodules.fifo = fifo = SyncFIFOBuffered(width=23 + 3 * 8 + 16 + 3, depth=64)
        m.d.comb += fifo_empty.eq(~fifo.r_rdy)

        assert self.perf_counters.depth_fifo_bucket.shape() == fifo.level[3:].shape(), \
            f"{self.perf_counters.depth_fifo_bucket.shape()} / {fifo.level[3:].shape()}"
        m.d.sync += [
            self.perf_counters.depth_fifo_bucket.eq(fifo.level[3:]),
        ]

        accept_interp = Signal()

        m.d.sync += [
            self.perf_counters.stalls.depth_load_addr.eq(interpolator.out_valid & ~z_reader.in_addr_ready),
            self.perf_counters.stalls.depth_fifo.eq(interpolator.out_valid & ~fifo.r_rdy),
        ]
        m.d.comb += [
            accept_interp.eq(fifo.w_rdy & z_reader.in_addr_ready),
            z_reader.in_addr.eq(self.z_base + interpolator.out_p_offset*2),

            interpolator.out_ready.eq(accept_interp),

            fifo.w_en.eq(interpolator.out_valid & z_reader.in_addr_ready),
            z_reader.in_addr_valid.eq(interpolator.out_valid & fifo.w_rdy),

            fifo.w_data.eq(Cat(
                interpolator.out_b,
                interpolator.out_g,
                interpolator.out_r,
                interpolator.out_z,
                interpolator.out_p_offset,
                interpolator.out_texture_buffer,
                interpolator.out_texture_enable,
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
                depth_tester.in_texture_buffer,
                depth_tester.in_texture_enable,
            ).eq(fifo.r_data),

            depth_tester.zst_valid.eq(z_reader.out_z_valid),
            z_reader.out_z_ready.eq(depth_tester.zst_ready),
            depth_tester.zst_z.eq(z_reader.out_z),
        ]

        accept_pix = Signal()

        m.d.sync += [
            self.perf_counters.stalls.depth_store_addr.eq(depth_tester.out_valid & ~self.axi2.write_address.ready),
            self.perf_counters.stalls.depth_store_data.eq(depth_tester.out_valid & ~self.axi2.write_data.ready),
            self.perf_counters.stalls.pixel_store.eq(depth_tester.out_valid & ~writer.ready),
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

            accept_pix.eq(texture_mapper.in_ready & self.axi2.write_data.ready & self.axi2.write_address.ready),

            self.axi2.write_address.valid.eq(
                depth_tester.out_valid & texture_mapper.in_ready & self.axi2.write_data.ready
            ),
            self.axi2.write_data.valid.eq(
                depth_tester.out_valid & texture_mapper.in_ready & self.axi2.write_address.ready
            ),

            depth_tester.out_ready.eq(accept_pix),
            texture_mapper.in_valid.eq(
                depth_tester.out_valid & self.axi2.write_data.ready & self.axi2.write_address.ready
            ),

            texture_mapper.in_p_offset.eq(depth_tester.out_p_offset),
            texture_mapper.in_r.eq(depth_tester.out_r),
            texture_mapper.in_g.eq(depth_tester.out_g),
            texture_mapper.in_b.eq(depth_tester.out_b),
            texture_mapper.in_texture_buffer.eq(depth_tester.out_texture_buffer),
            texture_mapper.in_texture_enable.eq(depth_tester.out_texture_enable),
        ]

        m.d.comb += [
            texture_mapper.out_ready.eq(writer.ready),
            writer.valid.eq(texture_mapper.out_valid),

            writer.p_offset.eq(texture_mapper.out_p_offset),
            writer.r.eq(texture_mapper.out_r),
            writer.g.eq(texture_mapper.out_g),
            writer.b.eq(texture_mapper.out_b),
        ]

        return m
