from amaranth import *
from amaranth.lib.wiring import Signature, In, Out


__all__ = ["TMDSEncoder"]


CONTROL_SYMBOLS = Array([
    C(0b0010101011, 10),  # C0 = 0, C1 = 0
    C(0b1101010100, 10),  # C0 = 1, C1 = 0
    C(0b0010101010, 10),  # C0 = 0, C1 = 1
    C(0b1101010101, 10),  # C0 = 1, C1 = 1
])


def _delay(m: Module, signal: Signal, cycles: int):
    flops = [Signal.like(signal, name="s{}_{}".format(i + 1, signal.name)) for i in range(cycles)]
    for i, o in zip((signal, *flops), flops):
        m.d.sync += o.eq(i)
    return flops[-1]


class TMDSEncoder(Elaboratable):
    signature = Signature({
        "data": In(8),
        "control": In(2),
        "data_enable": In(1),
        "output": Out(10),
    })

    def __init__(self):
        self.data = Signal(8)
        self.control = Signal(2)
        self.data_enable = Signal()
        self.output = Signal(10)

    def elaborate(self, platform):
        m = Module()

        # Stage 1
        s1_data = Signal.like(self.data)
        s1_ones = Signal(range(9))
        m.d.sync += [
            s1_data.eq(self.data),
            s1_ones.eq(sum(self.data)),
        ]

        # Stage 2
        s2_q_m = Signal(9)
        xnor = Signal()
        m.d.comb += xnor.eq((s1_ones > 4) | ((s1_ones == 4) & (s1_data[0] == 0)))

        m.d.sync += s2_q_m[0].eq(s1_data[0])

        curr = s1_data[0]
        for i in range(1, 8):
            curr = curr ^ s1_data[i] ^ xnor
            m.d.sync += s2_q_m[i].eq(curr)
        m.d.sync += s2_q_m[8].eq(~xnor)

        # Stage 3
        s3_q_m = Signal.like(s2_q_m)
        s3_ones = Signal(range(9))
        s3_zeros = Signal(range(9))
        m.d.sync += [
            s3_q_m.eq(s2_q_m),
            s3_ones.eq(sum(s2_q_m[:8])),
            s3_zeros.eq(sum(~s2_q_m[:8])),
        ]

        s3_control = _delay(m, self.control, 3)
        s3_data_enable = _delay(m, self.data_enable, 3)

        # Stage 4
        disparity = Signal(signed(6))
        data_disparity = Signal(signed(6))

        out_code = Signal(10)
        out_data = Signal(10)
        m.d.comb += out_code.eq(CONTROL_SYMBOLS[s3_control])
        with m.If((disparity == 0) | (s3_ones == s3_zeros)):
            m.d.comb += [
                out_data[9].eq(~s3_q_m[8]),
                out_data[8].eq(s3_q_m[8]),
                out_data[:8].eq(Mux(s3_q_m[8], s3_q_m[:8], ~s3_q_m[:8])),
                data_disparity.eq(Mux(s3_q_m[8], disparity + s3_ones - s3_zeros, disparity + s3_zeros - s3_ones)),
            ]
        with m.Else():
            with m.If(((disparity > 0) & (s3_ones > s3_zeros)) | ((disparity < 0) & (s3_zeros > s3_ones))):
                m.d.comb += [
                    out_data[9].eq(1),
                    out_data[8].eq(s3_q_m[8]),
                    out_data[:8].eq(~s3_q_m[:8]),
                    data_disparity.eq(disparity + 2*s3_q_m[8] + s3_zeros - s3_ones),
                ]
            with m.Else():
                m.d.comb += [
                    out_data[9].eq(0),
                    out_data[8].eq(s3_q_m[8]),
                    out_data[:8].eq(s3_q_m[:8]),
                    data_disparity.eq(disparity - 2*(~s3_q_m[8]) + s3_ones - s3_zeros),
                ]

        m.d.sync += [
            disparity.eq(Mux(s3_data_enable, data_disparity, 0)),
            self.output.eq(Mux(s3_data_enable, out_data, out_code)),
        ]

        return m
