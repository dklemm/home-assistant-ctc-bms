"""Modbus TCP access to the CTC controller.

Async port of the batching reader proven in dev/ctc_modbus_test.py, plus the
behaviours a long-running integration needs:

- One outstanding request at a time (asyncio.Lock): the controller stops
  answering entirely if requests are pipelined. Reads and writes share the lock.
- Reading a nonexistent register returns *silence*, not an exception, so it is
  indistinguishable from a dead link and costs a full timeout. Wanted addresses
  are therefore grouped into <=100-register block reads (a block costs the same
  ~10 ms as a single register), and a silent block is bisected rather than
  abandoned, so one absent address doesn't lose the other 99.
- An outage probe: when the first block of a poll is silent, one read of a
  guaranteed-live register decides "link down" (raise, one timeout) vs "absent
  address in the block" (bisect). Without it an unplugged pump would cost a
  bisection storm of timeouts every poll.
- A dead-address cache: addresses bisection proves absent are excluded from
  subsequent polls, so a map/firmware mismatch costs timeouts once, not forever.
"""

from __future__ import annotations

import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .const import MAX_BLOCK, PROBE_REGISTER

_LOGGER = logging.getLogger(__name__)


class CtcConnectionError(Exception):
    """The controller did not answer."""


class CtcHub:
    """Owns the Modbus client; all controller I/O goes through here."""

    def __init__(
        self,
        host: str,
        port: int,
        device_id: int,
        timeout: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        # retries=1: pymodbus defaults to 3, which triples the cost of every
        # absent address (each retry is a full timeout of silence).
        self._client = AsyncModbusTcpClient(
            host, port=port, timeout=timeout, retries=1
        )
        self._lock = asyncio.Lock()
        self.dead_addresses: set[int] = set()

    async def async_connect(self) -> None:
        if not await self._client.connect():
            raise CtcConnectionError(
                f"Could not connect to {self.host}:{self.port}"
            )

    def close(self) -> None:
        self._client.close()

    async def _read_block(self, address: int, count: int) -> list[int] | None:
        """One FC03 transaction. None = silence (absent address or dead link)."""
        async with self._lock:
            try:
                rr = await self._client.read_holding_registers(
                    address, count=count, device_id=self.device_id
                )
            except ModbusException:
                return None
            if rr.isError():
                return None
            return rr.registers

    async def async_probe(self) -> None:
        """One read of a register that exists on every CTC controller."""
        if await self._read_block(PROBE_REGISTER, 1) is None:
            raise CtcConnectionError(
                f"No response from {self.host}:{self.port} "
                f"(device id {self.device_id})"
            )

    async def async_read_addresses(self, addrs: set[int]) -> dict[int, int]:
        """Fetch many addresses efficiently. Missing keys = register absent.

        Raises CtcConnectionError when the controller answers nothing at all.
        """
        found: dict[int, int] = {}
        wanted = sorted(addrs - self.dead_addresses)
        if not wanted:
            return found

        async def fetch(lo: int, hi: int) -> None:
            span = hi - lo + 1
            words = await self._read_block(lo, span)
            if words is not None:
                for i, w in enumerate(words):
                    found[lo + i] = w
                return
            if span == 1:
                self.dead_addresses.add(lo)
                return
            mid = lo + span // 2
            await fetch(lo, mid - 1)
            await fetch(mid, hi)

        def gap_is_clear(a: int, b: int) -> bool:
            """No known-dead address strictly between two wanted addresses."""
            return not any(
                d in self.dead_addresses for d in range(a + 1, b)
            )

        # Only read spans that actually contain wanted addresses: walking the
        # whole min..max range would bisect through every dead gap in between.
        # Spans also break at cached dead addresses, otherwise a span covering
        # one would go silent and re-bisect every poll.
        first_span = True
        i = 0
        while i < len(wanted):
            j = i
            while (
                j + 1 < len(wanted)
                and wanted[j + 1] - wanted[i] < MAX_BLOCK
                and gap_is_clear(wanted[j], wanted[j + 1])
            ):
                j += 1
            lo, hi = wanted[i], wanted[j]
            words = await self._read_block(lo, hi - lo + 1)
            if words is not None:
                for k, w in enumerate(words):
                    found[lo + k] = w
            else:
                if first_span:
                    # Silence on the very first block: absent address, or is
                    # the whole link down? Decide with one probe read before
                    # committing to a bisection full of timeouts.
                    await self.async_probe()
                if hi > lo:
                    mid = lo + (hi - lo + 1) // 2
                    await fetch(lo, mid - 1)
                    await fetch(mid, hi)
                else:
                    self.dead_addresses.add(lo)
            first_span = False
            i = j + 1
        return found

    async def async_write_register(self, address: int, value: int) -> None:
        """Write one holding register with FC16, per the BMS manual."""
        async with self._lock:
            try:
                rr = await self._client.write_registers(
                    address, [value], device_id=self.device_id
                )
            except ModbusException as err:
                raise CtcConnectionError(
                    f"Write to register {address} failed: {err}"
                ) from err
        if rr.isError():
            raise CtcConnectionError(
                f"Controller rejected write to register {address}"
            )
