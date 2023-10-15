from amaranth import *
from amaranth.lib.wiring import Component, In, Out, Signature


__all__ = ["Divider"]


class Divider(Component):
    def __init__(self, width: int, unroll: int = 1):
        self._width = width
        self._unroll = unroll

        if (width % unroll) != 0:
            raise ValueError("Unroll factor must be a factor of width")

        super().__init__()

    @property
    def signature(self):
        return Signature({
            "n": In(unsigned(self._width)),
            "d": In(unsigned(self._width)),
            "trigger": In(1),

            "o": Out(unsigned(self._width)),
            "done": Out(1),
        })

    def elaborate(self, platform):
        m = Module()

        numerator = Signal(unsigned(self._width))
        denominator = Signal(unsigned(self._width))
        remainder = Signal(unsigned(self._width))
        running = Signal()

        # https://github.com/SpinalHDL/VexRiscv/blob/4e051ed2a3d5818ba2476195cb727c04301d289f/src/main/scala/vexriscv/plugin/MulDivIterativePlugin.scala
        def stages(in_numerator, in_remainder, stage):
            if stage == 0:
                m.d.sync += [
                    numerator.eq(in_numerator),
                    remainder.eq(in_remainder),
                ]
                return
            remainder_shifted = Cat(in_numerator[-1], in_remainder).as_unsigned()
            remainder_minus_denominator = remainder_shifted - denominator
            out_remainder = Mux(
                remainder_minus_denominator[-1],
                remainder_shifted[:self._width],
                remainder_minus_denominator[:self._width],
            )
            out_numerator = Cat(~remainder_minus_denominator[-1], in_numerator).as_unsigned()[:self._width]

            stages(out_numerator, out_remainder, stage - 1)

        stages(numerator, remainder, self._unroll)

        counter = Signal(range(self._width // self._unroll + 2))

        m.d.comb += self.done.eq(running & (counter == self._width // self._unroll))
        m.d.comb += self.o.eq(numerator)

        m.d.sync += counter.eq(counter + 1)
        with m.If(self.done):
            m.d.sync += running.eq(0)
        with m.If(self.trigger):
            m.d.sync += [
                running.eq(1),
                counter.eq(0),
                numerator.eq(self.n),
                denominator.eq(self.d),
                remainder.eq(0),
            ]

        return m
