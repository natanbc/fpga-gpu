from amaranth import *
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, Signature
from .zynq_ifaces import SAxiHP


ControlRegisters = Signature({
    "base_addr": Out(32),
    # 1920 * 1080 * 3 / 8 == 777600, ceil(log2(777600)) = 20
    "words": Out(20),
    "trigger": Out(1),
    "idle": In(1),
})


PixelStream = Signature({
    "valid": Out(1),
    "ready": In(1),
    # TODO: lib.data
    "pixel": Out(24),
})


class PixelFIFO(Elaboratable):
    # TODO: using Component.__init__() confuses the IDE
    signature = Signature({
        "axi_read": Out(SAxiHP.members["read"].signature),
        "pixel_stream": Out(PixelStream),
    })

    def __init__(self):
        self.axi_read = SAxiHP.members["read"].signature.create()
        self.pixel_stream = PixelStream.create()

    def elaborate(self, platform):
        m = Module()

        # 4KiB buffer (exact size of a series 7 BRAM (512x64bit))
        m.submodules.fifo = fifo = SyncFIFOBuffered(width=64, depth=4096 // 8)

        m.d.comb += [
            self.axi_read.ready.eq(fifo.w_rdy),
            fifo.w_data.eq(self.axi_read.data),
            fifo.w_en.eq(self.axi_read.valid),
        ]

        ctr = Signal(range(4))
        remain = Signal(16)

        m.d.comb += [
            self.pixel_stream.valid.eq(fifo.r_rdy),
        ]
        with m.FSM(name="fifo_adapter"):
            # fifo word = AAABBBCC, remain = XXXXXXXX
            with m.State("A"):
                m.d.comb += self.pixel_stream.pixel.eq(fifo.r_data.word_select(ctr, 24))
                with m.If(fifo.r_rdy & self.pixel_stream.ready):
                    m.d.sync += ctr.eq(ctr + 1)
                    with m.If(ctr == 1):
                        m.next = "B"
                        m.d.comb += fifo.r_en.eq(1)
                        m.d.sync += ctr.eq(0), remain.eq(fifo.r_data[48:])
            # fifo word = CDDDEEEF, remain = XXXXXXCC
            with m.State("B"):
                with m.Switch(ctr):
                    with m.Case(0):
                        m.d.comb += self.pixel_stream.pixel.eq(Cat(remain, fifo.r_data[:8]))
                    with m.Case(1, 2):
                        m.d.comb += self.pixel_stream.pixel.eq(fifo.r_data[8:].word_select((ctr-1).as_unsigned(), 24))
                with m.If(fifo.r_rdy & self.pixel_stream.ready):
                    m.d.sync += ctr.eq(ctr + 1)
                    with m.If(ctr == 2):
                        m.next = "C"
                        m.d.comb += fifo.r_en.eq(1)
                        m.d.sync += ctr.eq(0), remain.eq(fifo.r_data[56:])
            # fifo word = FFGGGHHH, remain = XXXXXXXF
            with m.State("C"):
                with m.Switch(ctr):
                    with m.Case(0):
                        m.d.comb += self.pixel_stream.pixel.eq(Cat(remain[:8], fifo.r_data[:16]))
                    with m.Case(1, 2):
                        m.d.comb += self.pixel_stream.pixel.eq(fifo.r_data[16:].word_select((ctr-1).as_unsigned(), 24))
                with m.If(fifo.r_rdy & self.pixel_stream.ready):
                    m.d.sync += ctr.eq(ctr + 1)
                    with m.If(ctr == 2):
                        m.next = "A"
                        m.d.comb += fifo.r_en.eq(1)
                        m.d.sync += ctr.eq(0), remain.eq(0)

        return m


class AxiReader(Elaboratable):
    signature = Signature({
        "axi_address": Out(SAxiHP.members["read_address"].signature),
        "control": In(ControlRegisters),
    })

    def __init__(self):
        self.axi_address = SAxiHP.members["read_address"].signature.create()
        self.control = ControlRegisters.flip().create()

    def elaborate(self, platform):
        m = Module()

        addr = Signal.like(self.control.base_addr)
        ctr = Signal.like(self.control.words)
        burst_len = Signal(4)

        with m.If(ctr == 0):
            m.d.comb += self.control.idle.eq(1)
            with m.If(self.control.trigger):
                m.d.sync += [
                    addr.eq(self.control.base_addr),
                    ctr.eq(self.control.words),
                ]
        with m.Else():
            m.d.comb += [
                burst_len.eq(Mux(ctr >= 16, 0b1111, ctr - 1)),
                self.axi_address.valid.eq(1),
                self.axi_address.burst.eq(0b01),  # INCR
                self.axi_address.size.eq(0b11),   # 8 bytes/beat
                self.axi_address.addr.eq(addr),
                self.axi_address.len.eq(burst_len),
                self.axi_address.qos.eq(0b1111),  # Max priority
            ]
            with m.If(self.axi_address.ready):
                m.d.sync += [
                    ctr.eq(ctr - burst_len - 1),
                    addr.eq(addr + 8 * (burst_len + 1)),
                ]

        return m


class AxiFramebuffer(Elaboratable):
    signature = Signature({
        "axi": Out(SAxiHP),
        "control": In(ControlRegisters),
        "pixel_stream": Out(PixelStream),
    })

    def __init__(self):
        self.axi = SAxiHP.create()
        self.control = ControlRegisters.create()
        self.pixel_stream = PixelStream.create()

    def elaborate(self, platform):
        m = Module()

        m.submodules.pixel_fifo = pixel_fifo = PixelFIFO()
        m.submodules.axi_reader = axi_reader = AxiReader()

        # TODO: reset
        m.d.comb += self.axi.aclk.eq(ClockSignal())

        wiring.connect(m, wiring.flipped(self.axi.read), pixel_fifo.axi_read)
        wiring.connect(m, self.control, axi_reader.control)
        wiring.connect(m, wiring.flipped(self.axi.read_address), axi_reader.axi_address)
        wiring.connect(m, wiring.flipped(self.pixel_stream), pixel_fifo.pixel_stream)

        return m
