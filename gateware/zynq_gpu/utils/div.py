from amaranth import *
from amaranth.lib.wiring import Component, In, Out


__all__ = ["Divider"]


# TODO: proper divider
class Divider(Component):
    n: In(signed(32))
    d: In(signed(32))
    trigger: In(1)

    o: Out(signed(32))
    done: Out(1)

    def elaborate(self, platform):
        m = Module()

        m.d.sync += [
            self.o.eq(self.n // self.d),
            self.done.eq(self.trigger),
        ]

        return m
