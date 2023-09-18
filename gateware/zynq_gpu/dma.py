from amaranth import *
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, Signature
from amaranth.utils import log2_int
from .zynq_ifaces import SAxiHP


__all__ = ["ControlRegisters", "DataStream", "DMA", "DMAFifo", "DMAControl"]


ControlRegisters = Signature({
    "base_addr": Out(25),   # 128-byte aligned base address. The 7 LSBs are filled with zeroes.
    "words": Out(20),       # How many words of data should be read.
    "trigger": Out(1),      # Start a transaction. Does nothing if `request_done == 0`.
    "idle": In(1),          # Whether the memory side is idle. The FIFO may still have data, but all bursts are done,
                            # so it's safe to modify the buffer.
    "request_done": In(1),  # Whether all requests have been sent. The data might still not have been read,
                            # so the buffer should not be modified, but a new transfer may be started.
    "qos": Out(4),          # AXI QOS field.
})


DataStream = Signature({
    "valid": Out(1),
    "ready": In(1),
    "data": Out(64),
})


class DMAFifo(Elaboratable):
    def __init__(self, depth):
        self._depth = depth

        self.axi_read = SAxiHP.members["read"].signature.create()
        self.data_stream = DataStream.create()
        self.fifo_level = Signal(log2_int(depth + 1, need_pow2=False))
        self.burst_end = Signal(1)

    @property
    def signature(self):
        return Signature({
            "axi_read": Out(SAxiHP.members["read"].signature),
            "data_stream": Out(DataStream),
            "fifo_level": Out(log2_int(self._depth + 1, need_pow2=False)),
            "burst_end": Out(1),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.fifo = fifo = SyncFIFOBuffered(width=64, depth=self._depth)

        assert self.fifo_level.shape() == fifo.r_level.shape()
        m.d.comb += self.fifo_level.eq(fifo.r_level)

        m.d.comb += [
            self.axi_read.ready.eq(fifo.w_rdy),
            fifo.w_data.eq(self.axi_read.data),
            fifo.w_en.eq(self.axi_read.valid),

            self.burst_end.eq(self.axi_read.ready & self.axi_read.valid & self.axi_read.last),

            self.data_stream.valid.eq(fifo.r_rdy),
            self.data_stream.data.eq(fifo.r_data),
            fifo.r_en.eq(self.data_stream.ready),
        ]

        return m


class DMAControl(Elaboratable):
    signature = Signature({
        "axi_address": Out(SAxiHP.members["read_address"].signature),
        "control": In(ControlRegisters),
        "burst_end": In(1),
    })

    def __init__(self, *, max_pending_bursts):
        self._max_pending = max_pending_bursts

        self.axi_address = SAxiHP.members["read_address"].signature.create()
        self.control = ControlRegisters.flip().create()
        self.burst_end = Signal(1)

    def elaborate(self, platform):
        m = Module()

        addr_128 = Signal.like(self.control.base_addr)
        ctr = Signal.like(self.control.words)

        pending_bursts = Signal(range(self._max_pending))
        sent_burst = Signal()
        m.d.sync += pending_bursts.eq(
            pending_bursts +
            Mux(sent_burst, 1, 0) +
            Mux(self.burst_end, -1, 0)
        )

        with m.If(ctr == 0):
            m.d.comb += [
                self.control.request_done.eq(1),
                self.control.idle.eq(pending_bursts == 0),
            ]
            with m.If(self.control.trigger):
                m.d.sync += [
                    addr_128.eq(self.control.base_addr),
                    ctr.eq(self.control.words),
                ]
        with m.Else():
            m.d.comb += self.axi_address.valid.eq(~(pending_bursts.all()))

        burst_len = Signal(4)
        m.d.comb += [
            burst_len.eq(Mux(ctr[4:].any(), 0b1111, ctr - 1)),
            self.axi_address.burst.eq(0b01),  # INCR
            self.axi_address.size.eq(0b11),   # 8 bytes/beat
            self.axi_address.addr.eq(Cat(C(0, 7), addr_128)),
            self.axi_address.len.eq(burst_len),
            self.axi_address.qos.eq(self.control.qos),

            sent_burst.eq(self.axi_address.ready & self.axi_address.valid),
        ]
        with m.If(sent_burst):
            m.d.sync += [
                # If burst length is less than 16 words, it's the last burst so counter
                # would get zeroed.
                ctr.eq(Mux(burst_len == 0b1111, ctr - 16, 0)),
                # Doesn't matter if it's left wrong after the last access
                addr_128.eq(Mux(burst_len == 0b1111, addr_128 + 1, addr_128)),
            ]

        return m


class DMA(Elaboratable):
    def __init__(self, max_pending_bursts=64, fifo_depth=512):
        if max_pending_bursts & (max_pending_bursts - 1):
            raise ValueError(f"Max pending bursts must be a power of two, not {max_pending_bursts!r}")
        if fifo_depth & (fifo_depth - 1):
            raise ValueError("FIFO bytes must be a power of two")

        self._max_pending = max_pending_bursts
        self._fifo_depth = fifo_depth

        self.axi = SAxiHP.create()
        self.control = ControlRegisters.create()
        self.data_stream = DataStream.create()
        self.fifo_level = Signal(log2_int(fifo_depth + 1, need_pow2=False))

    @property
    def signature(self):
        return Signature({
            "axi": Out(SAxiHP),
            "control": In(ControlRegisters),
            "data_stream": Out(DataStream),
            "fifo_level": Out(log2_int(self._fifo_depth + 1, need_pow2=False)),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.fifo = fifo = DMAFifo(depth=self._fifo_depth)
        m.submodules.control = control = DMAControl(max_pending_bursts=self._max_pending)

        # TODO: reset
        m.d.comb += self.axi.aclk.eq(ClockSignal())

        wiring.connect(m, wiring.flipped(self.axi.read), fifo.axi_read)
        wiring.connect(m, self.control, control.control)
        wiring.connect(m, wiring.flipped(self.axi.read_address), control.axi_address)
        wiring.connect(m, wiring.flipped(self.data_stream), fifo.data_stream)

        m.d.comb += [
            control.burst_end.eq(fifo.burst_end),
            self.fifo_level.eq(fifo.fifo_level),
        ]

        return m
