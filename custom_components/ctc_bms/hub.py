"""Modbus TCP access to the CTC controller.

Async port of the batching reader proven in dev/ctc_modbus_test.py, plus the
behaviours a long-running integration needs:

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

The transport is modbus-connection over pymodbus, which serializes requests
over the link - the controller stops answering entirely if they are pipelined,
so nothing here may issue two at once. Only two errors mean "absent address":
silence (ModbusTimeoutError), which is what the CTC does, and exception code 2
(IllegalDataAddressError), which is what a device that answers politely does.
Every other ModbusError is a link or device failure and must not feed the
bisection, or one blip would cache live registers as dead for ever.
"""

from __future__ import annotations

from modbus_connection import (
    IllegalDataAddressError,
    ModbusError,
    ModbusTcpParams,
    ModbusTimeoutError,
)
from modbus_connection.pymodbus import ModbusConnection

from .const import MAX_BLOCK, PROBE_REGISTER


class CtcConnectionError(Exception):
    """The controller did not answer."""


class CtcHub:
    """Owns the Modbus connection; all controller I/O goes through here."""

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
        # Constructing a connection performs no I/O; the first read opens the
        # link, and a later one re-opens it if it drops. The backend sets
        # retries=0, so an absent address costs exactly one timeout.
        self._connection = ModbusConnection(
            ModbusTcpParams(host=host, port=port), timeout=timeout
        )
        self._unit = self._connection.for_unit(device_id)
        self.dead_addresses: set[int] = set()

    async def async_close(self) -> None:
        await self._connection.close()

    async def _read_block(self, address: int, count: int) -> list[int] | None:
        """One FC03 transaction. None = the block holds an absent address.

        The CTC answers one with silence (ModbusTimeoutError). A device that is
        more polite - the simulator, another controller's firmware - says so
        with exception code 2 instead; same meaning, same handling.

        Everything else raises: a dropped link, a closed connection, a busy or
        failed device is not an absent address, and bisecting one would cache
        live addresses as dead for ever.
        """
        try:
            return await self._unit.read_holding_registers(address, count)
        except (ModbusTimeoutError, IllegalDataAddressError):
            return None
        except ModbusError as err:
            # The library's message already names the call and the endpoint.
            raise CtcConnectionError(str(err)) from err

    async def async_probe(self) -> None:
        """One read of a register that exists on every CTC controller.

        Doubles as the connect: the read opens the link, so a host that refuses
        the socket and a device id that answers nothing both surface here.
        """
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
        try:
            await self._unit.write_registers(address, [value])
        except ModbusError as err:
            # Includes the controller's refusal code when it rejected the value.
            raise CtcConnectionError(f"Write to {address} failed: {err}") from err
