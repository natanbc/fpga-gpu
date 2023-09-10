import os
import subprocess

from amaranth.build import *
from amaranth.vendor import XilinxPlatform
from amaranth_boards.resources import *


__all__ = ["EBAZ4205Platform"]


class EBAZ4205Platform(XilinxPlatform):
    device      = "xc7z010"
    package     = "clg400"
    speed       = "1"

    resources = [
        # Not all boards have this oscillator populated
        Resource("clk50", 0, Pins("N18", dir="i"), Clock(50e6), Attrs(IOSTANDARD="LVCMOS33")),

        *LEDResources(pins="W13 W14", invert=True, attrs=Attrs(IOSTANDARD="LVCMOS33")),

        Resource("enet", 0,
                 Subsignal("tx_data", Pins("W18 Y18 V18 Y19", dir="o")),
                 Subsignal("rx_data", Pins("Y16 V16 V17 Y17", dir="i")),
                 Subsignal("tx_en", Pins("W19", dir="o")),
                 Subsignal("rx_dv", Pins("W16", dir="i")),
                 Subsignal("tx_clk", Pins("U15", dir="i")),
                 Subsignal("rx_clk", Pins("U14", dir="i")),
                 Subsignal("mdc", Pins("W15", dir="o")),
                 Subsignal("mdio", Pins("Y14", dir="io")),
                 Attrs(IOSTANDARD="LVCMOS33")),

        Resource("j", 3,
                 Subsignal("speed", Pins("V13", dir="i")),
                 Subsignal("pwm", Pins("U12", dir="o")),
                 Attrs(IOSTANDARD="LVCMOS33")),

        Resource("j", 4,
                 Subsignal("speed", Pins("V15", dir="i")),
                 Subsignal("pwm", Pins("V12", dir="o")),
                 Attrs(IOSTANDARD="LVCMOS33")),
    ]

    connectors = [
        Connector("data", 1, "- - - - A20 H16 B19 B20 C20 - H17 - D20 D18 H18 D19 F20 E19 F19 K17"),
        Connector("data", 2, "- - - - G20 J18 G19 H20 J19 - K18 - K19 J20 L16 L19 M18 L20 M20 L17"),
        Connector("data", 3, "- - - - M19 N20 P18 M17 N17 - P20 - R18 R19 P19 T20 U20 T19 V20 U19"),

        Connector("j", 3, "- - V13 U12"),  # GND VCC SPEED(input) PWM(output)
        Connector("j", 4, "- - V15 V12"),  # GND VCC SPEED(input) PWM(output)
    ]

    _extension_board = [
        Resource("led", 2, Pins("E19", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),
        Resource("led", 3, Pins("K17", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),
        Resource("led", 4, Pins("H18", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),

        *ButtonResources(pins="T19 P19 U20 U19 V20", invert=True, attrs=Attrs(IOSTANDARD="LVCMOS33")),

        UARTResource(0, rx="H16", tx="H17", attrs=Attrs(IOSTANDARD="LVCMOS33")),

        Resource("hdmi_tx", 0,
                 Subsignal("clk", DiffPairs(p="F19", n="F20", dir="o"), Attrs(IOSTANDARD="TMDS_33")),
                 #  Subsignal("d", DiffPairs(p="C20 D19 B19", n="B20 D20 A20", dir="o"), Attrs(IOSTANDARD="TMDS_33"))),
                Subsignal("d", DiffPairs(p="D19 C20 B19", n="D20 B20 A20", dir="o"), Attrs(IOSTANDARD="TMDS_33"))),

        Resource("buzzer", 0,
                 Pins("D18", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),
    ]

    _extension_board_lcd = [
        Resource("lcd", 0,
                 Subsignal("backlight", Pins("T20", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),
                 Subsignal("dc", Pins("R18", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),
                 Subsignal("scl", Pins("R19", dir="o"), Attrs(IOSTANDARD="LVCMOS33")),
                 Subsignal("sda", Pins("P20", dir="io"), Attrs(IOSTANDARD="LVCMOS33")),
                 Subsignal("res", Pins("N17", dir="o", invert=True), Attrs(IOSTANDARD="LVCMOS33"))),
    ]

    _extension_board_conns = [
        Connector("h", 4, "K18 J18 G19 G20 J20 H20 K19 J19 L20 L19 L17 L16 M20 M18 M19 N20 P18 M17 - -"),
    ]

    # https://aliexpress.com/item/1005004052650225.html
    def with_extension_board(self, with_lcd):
        self.add_resources(self._extension_board)
        if with_lcd:
            self.add_resources(self._extension_board_lcd)
        self.add_connectors(self._extension_board_conns)

        return self

    @property
    def required_tools(self):
        return super().required_tools + [
            "openFPGALoader"
        ]

    def toolchain_program(self, products, name, **kwargs):
        tool = os.environ.get("OPENFPGALOADER", "openFPGALoader")
        with products.extract("{}.bin".format(name)) as bitstream_filename:
            subprocess.check_call([tool, "-c", "ft232", "--index-chain", "1", "-m", bitstream_filename])
