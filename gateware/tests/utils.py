import itertools

from amaranth.sim import Passive, Settle
from collections import deque
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Optional, TypeVar


__all__ = ["wait_until", "AxiEmulator", "make_testbench_process"]


def wait_until(signal, max_cycles=1000):
    for i in range(max_cycles):
        if (yield signal):
            return
        yield
    raise Exception("Took too long")


def make_testbench_process(proc):
    def f():
        generator = proc()
        try:
            yield Settle()
            command = generator.send(None)
            while True:
                try:
                    result = yield command
                except Exception as e:
                    generator.throw(e)
                    continue
                yield Settle()
                command = generator.send(result)
        except StopIteration:
            pass
    return f


T = TypeVar("T")


class Queue(Generic[T]):
    def __init__(self, max_size: int):
        if max_size < 1:
            raise ValueError("Max size must be at least 1")
        self._deque = deque()
        self._max_size = max_size

    def w_rdy(self):
        return len(self._deque) < self._max_size

    def write(self, data: T):
        if len(self._deque) >= self._max_size:
            raise ValueError("Write to full queue")
        self._deque.append(data)

    def r_rdy(self):
        return len(self._deque) > 0

    def read(self) -> T:
        return self._deque.popleft()


@dataclass
class AxiAddress:
    address: int
    burst_type: int
    bytes_per_beat: int
    burst_length: int
    id: int
    clock: int


@dataclass
class AxiWriteData:
    data: int
    strb: int
    last: bool
    id: int


@dataclass
class AxiWriteResponse:
    resp: int
    id: int


def _wrap(c):
    def f():
        yield Passive()
        yield from c()
    return make_testbench_process(f)


def _addr_gen(addr: AxiAddress) -> Iterable[int]:
    assert addr.address % addr.bytes_per_beat == 0, "Unaligned accesses are not supported"
    match addr.burst_type:
        case 0b00:  # FIXED
            return itertools.repeat(addr.address, addr.burst_length)
        case 0b01:  # INCR
            start_addr = addr.address
            end_addr = addr.address + addr.bytes_per_beat * addr.burst_length - 1
            assert (start_addr & ~0xFFF) == (end_addr & ~0xFFF), f"Start and end addresses cross 4KiB boundary"

            return (addr.address + addr.bytes_per_beat * i for i in range(addr.burst_length))
        case 0b10:  # WRAP
            assert addr.address % addr.bytes_per_beat == 0
            assert addr.burst_length in (2, 4, 8, 16)
            wrap_mask = (addr.bytes_per_beat * addr.burst_length) - 1
            low_bits = addr.address & wrap_mask
            wrap_address = addr.address & ~wrap_mask

            return (
                wrap_address | ((low_bits + addr.bytes_per_beat * i) & wrap_mask)
                for i in range(addr.burst_length)
            )
        case 0b11:
            raise ValueError("Invalid burst type")
        case _:
            raise AssertionError("Impossible")


class AxiEmulator:
    def __init__(self, interface,
                 # (addr, bytes_per_beat) -> value
                 read: Optional[Callable[[int, int], int]],
                 # (addr, bytes_per_beat, value, strb)
                 write: Optional[Callable[[int, int, int, int], None]],
                 ar_buffer: int = 1,
                 aw_buffer: int = 1,
                 w_buffer: int = 1,
                 read_latency: int = 1,
                 write_latency: int = 1):
        if read is None and write is None:
            raise ValueError("At least one of read/write functions should be present")
        if read_latency < 1:
            raise ValueError("Read latency must be at least 1")
        if write_latency < 1:
            raise ValueError("Write latency must be at least 1")
        self._clock = 0

        self._iface = interface
        self._ar_q = Queue[AxiAddress](ar_buffer)
        self._aw_q = Queue[AxiAddress](aw_buffer)
        self._w_q = Queue[AxiWriteData](w_buffer)
        self._read = read
        self._write = write
        self._read_latency = read_latency
        self._write_latency = write_latency
        self._added = False

    def add_to_sim(self, sim, domain="sync"):
        if self._added:
            raise ValueError("Simulator can only be used once")
        self._added = True

        for p in [self._ar, self._r, self._aw, self._w, self._b, self._clock_counter]:
            sim.add_sync_process(_wrap(p), domain=domain)

    def _clock_counter(self):
        while True:
            yield
            self._clock += 1

    def _address_channel(self, channel, queue, is_read):
        while True:
            while True:
                yield channel.ready.eq(queue.w_rdy())
                yield Settle()
                if (yield channel.ready) == 1 and (yield channel.valid) == 1:
                    break
                else:
                    yield
            if is_read:
                assert self._read is not None, "Attempt to read from write only emulator"
            else:
                assert self._write is not None, "Attempt to write to read only emulator"
            queue.write(AxiAddress(
                (yield channel.addr),
                (yield channel.burst),
                1 << (yield channel.size),
                (yield channel.len) + 1,
                (yield channel.id),
                self._clock + (self._read_latency if is_read else self._write_latency),
            ))
            yield

    def _ar(self):
        yield from self._address_channel(self._iface.read_address, self._ar_q, True)

    def _r(self):
        r = self._iface.read
        while True:
            while not self._ar_q.r_rdy():
                yield
            op = self._ar_q.read()
            while self._clock < op.clock:
                yield
            for i, addr in enumerate(_addr_gen(op)):
                value = self._read(addr, op.bytes_per_beat)
                yield r.valid.eq(1)
                yield r.id.eq(op.id)
                yield r.data.eq(value)
                yield r.last.eq(i == op.burst_length - 1)
                while True:
                    done = (yield r.ready)
                    yield
                    if done:
                        break
            yield r.valid.eq(0)
            yield r.id.eq(0)
            yield r.data.eq(0)
            yield r.last.eq(0)

    def _aw(self):
        yield from self._address_channel(self._iface.write_address, self._aw_q, False)

    def _w(self):
        w = self._iface.write_data
        while True:
            while True:
                yield w.ready.eq(self._w_q.w_rdy())
                yield Settle()  # why is this needed?
                if (yield w.valid) and (yield w.ready):
                    break
                else:
                    yield
            self._w_q.write(AxiWriteData(
                (yield w.data),
                (yield w.strb),
                (yield w.last) == 1,
                (yield w.id),
            ))
            yield

    def _b(self):
        b = self._iface.write_response
        while True:
            yield b.valid.eq(0)
            while not self._aw_q.r_rdy():
                yield
            op = self._aw_q.read()
            while self._clock < op.clock:
                yield
            for i, addr in enumerate(_addr_gen(op)):
                while not self._w_q.r_rdy():
                    yield
                data = self._w_q.read()
                assert data.id == op.id, "Interleaved transactions not supported yet"
                assert data.last == (i == op.burst_length - 1)
                self._write(addr, op.bytes_per_beat, data.data, data.strb)
            yield b.valid.eq(1)
            yield b.id.eq(op.id)
            while not (yield b.ready):
                yield
