from amaranth import *
from amaranth.lib import wiring
from ..rasterizer import PipelinedRasterizer as Rasterizer, PerfCounters
from ..rasterizer.buffer_clearer import BufferClearer
from ..rasterizer.command_processor import CommandProcessor
from ..rasterizer.texture_buffer import TextureBuffer
from ..zynq_ifaces import SAxiGP, SAxiHP
from .peripheral import Peripheral


__all__ = ["Raster"]


class Raster(Peripheral):
    def __init__(self, width, *, name=None, src_loc_at=1):
        super().__init__(name=name, src_loc_at=src_loc_at)

        self._width = width

        self.axi1 = SAxiHP.create()
        self.axi2 = SAxiHP.create()
        self.axi3 = SAxiHP.create()
        self.axi_cmd = SAxiGP.create()

        self._fb_base = self.csr(32, "rw")
        self._z_base = self.csr(32, "rw")
        self._idle = self.csr(1, "r")
        self._cmd_addr_64 = self.csr(26, "rw")
        self._cmd_words = self.csr(20, "rw")
        self._cmd_ctrl = self.csr(1, "rw")
        self._cmd_dma_idle = self.csr(1, "r")
        self._cmd_idle = self.csr(1, "r")

        self._perf_counters = perf_counters = PerfCounters(Signal(PerfCounters))

        self._stall_ctrs = {
            0: (self.csr(32, "r", name="perf_counter_busy_cycles"), perf_counters.busy),
        }
        for i, r in enumerate(["walker_searching", "walker", "depth_load_addr", "depth_fifo", "depth_store_addr",
                               "depth_store_data", "pixel_store"]):
            self._stall_ctrs[len(self._stall_ctrs)] = (
                self.csr(32, "r", name=f"perf_counter_stall_{r}"),
                getattr(perf_counters.stalls, r),
            )
        self._stall_fifo_buckets = {}
        for i in range(9):
            level = f"{8*i}_{8*(i+1)-1}" if i < 8 else "full"
            self._stall_fifo_buckets[len(self._stall_fifo_buckets)] = (
                self.csr(32, "r", name=f"perf_counter_fifo_level_bucket_{level}"))

        self._cmd_done = self.irq()
        self._cmd_dma_done = self.irq()

        self._bridge = self.bridge()
        self.bus = self._bridge.bus
        self.irq = self._bridge.irq

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge = self._bridge
        m.submodules.rasterizer = rasterizer = Rasterizer()
        wiring.connect(m, rasterizer.axi, wiring.flipped(self.axi1))
        wiring.connect(m, rasterizer.axi2, wiring.flipped(self.axi2))

        m.d.comb += rasterizer.width.eq(self._width)

        m.submodules.command_processor = command_processor = CommandProcessor()
        wiring.connect(m, command_processor.axi, wiring.flipped(self.axi_cmd))
        wiring.connect(m, command_processor.triangles, rasterizer.triangles)

        m.submodules.texture_buffer = texture_buffer = TextureBuffer()
        wiring.connect(m, command_processor.texture_writes, texture_buffer.write)
        wiring.connect(m, rasterizer.texture_read, texture_buffer.read)

        m.submodules.buffer_clearer = buffer_clearer = BufferClearer()
        wiring.connect(m, buffer_clearer.axi, wiring.flipped(self.axi3))
        wiring.connect(m, command_processor.buffer_clears, buffer_clearer.control),

        m.d.comb += [
            command_processor.rasterizer_idle.eq(rasterizer.idle),
            command_processor.clearer_idle.eq(buffer_clearer.idle),
        ]

        for reg, field in zip(
                [self._fb_base, self._z_base, self._cmd_addr_64, self._cmd_words],
                [
                    rasterizer.fb_base, rasterizer.z_base,
                    command_processor.control.base_addr, command_processor.control.words
                ],
        ):
            m.d.comb += reg.r_data.eq(field)
            with m.If(reg.w_stb):
                m.d.sync += field.eq(reg.w_data)

        for a, b in zip(
                [self._idle, self._cmd_dma_idle, self._cmd_idle],
                [rasterizer.idle, command_processor.control.idle, command_processor.idle],
        ):
            m.d.comb += a.r_data.eq(b)

        ctrl = Signal()
        m.d.comb += self._cmd_ctrl.r_data.eq(ctrl)
        with m.If(self._cmd_ctrl.w_stb):
            m.d.sync += ctrl.eq(self._cmd_ctrl.w_data)

        ctrl_last = Signal()
        m.d.sync += ctrl_last.eq(ctrl)
        m.d.comb += command_processor.control.trigger.eq(ctrl_last ^ ctrl)

        m.d.comb += self._perf_counters.eq(rasterizer.perf_counters)
        for i, (r, bit) in self._stall_ctrs.items():
            m.d.sync += r.r_data.eq(r.r_data + bit)
        for i, r in self._stall_fifo_buckets.items():
            with m.If(self._perf_counters.depth_fifo_bucket == i):
                m.d.sync += r.r_data.eq(r.r_data + 1)

        cmd_idle_prev = Signal()
        m.d.sync += cmd_idle_prev.eq(command_processor.idle)
        m.d.comb += self._cmd_done.eq(command_processor.idle & ~cmd_idle_prev)

        cmd_dma_idle_prev = Signal()
        m.d.sync += cmd_dma_idle_prev.eq(command_processor.control.idle)
        m.d.comb += self._cmd_dma_done.eq(command_processor.control.idle & ~cmd_dma_idle_prev)

        return m
