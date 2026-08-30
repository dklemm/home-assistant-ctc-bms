"""Keeping a 1000-range control asserted.

The control registers are discarded by the controller if they are not
re-written within five minutes, so "set" is not a thing you do once - it is a
thing you keep doing. This module is the only place that repeats a write.

That makes it the first part of the integration to write without a service call
behind it, which is why it is gated on its own option (CONF_CONTROLS) and why
the refresh is the *only* self-started write there is: polling stays read-only,
and nothing here ever touches a stored parameter.

Three properties are load-bearing:

- **Refresh, don't latch.** REFRESH is 60 s against the controller's 300 s
  window, so four refreshes can be missed before anything is lost. It is the
  same margin hold_write() uses in dev/ctc_modbus_test.py.
- **Expiry is mirrored, not tracked separately.** A hold that has not been
  written successfully for EXPIRY seconds is dropped and its entity goes back
  to reading unknown - because by then the controller has certainly dropped it
  too. A link that is down for six minutes leaves HA telling the truth without
  a failure counter or a reconnect hook.
- **Nothing is re-asserted at startup.** The five-minute expiry is the
  fail-safe; honouring it means never commanding a live heating system with no
  human in the loop. A restart, a reload or an options change all come up with
  nothing held.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

import homeassistant.util.dt as dt_util

from .controls import VDI_REGISTER
from .hub import CtcConnectionError, CtcHub

_LOGGER = logging.getLogger(__name__)

# The controller's own window, and how often we beat it.
EXPIRY = timedelta(minutes=5)
REFRESH = timedelta(seconds=60)


class ControlHold:
    """The words currently asserted on the controller, and the timer doing it.

    One word per register, however many entities sit on top: the eight virtual
    digital inputs on 1100 read-modify-write the same held word, so they can
    never fight each other. Two *masters* still can - disable the config entry
    before running the CLI's `probe` or `discover-di`.
    """

    def __init__(self, hass: HomeAssistant, hub: CtcHub) -> None:
        self._hass = hass
        self._hub = hub
        self._held: dict[int, int] = {}
        self._last_ok: dict[int, datetime] = {}
        self._listeners: list[Callable[[], None]] = []
        self._cancel_timer: Callable[[], None] | None = None

    # -- state ------------------------------------------------------------

    def word_for(self, number: int) -> int | None:
        """The word being asserted, or None when the control is released."""
        return self._held.get(number)

    @property
    def held(self) -> dict[int, int]:
        """Every asserted control, for diagnostics."""
        return dict(self._held)

    @callback
    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to changes. Control state never comes from a poll, so
        entities listen here instead of on the coordinator."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # -- commands ---------------------------------------------------------

    async def async_set(self, number: int, word: int) -> None:
        """Assert a word now, and keep asserting it until released.

        Unlike CtcEntity.async_write_raw this does *not* skip a write that
        matches what is already held: re-writing is the whole point, and the
        register costs nothing to write.
        """
        await self._write(number, word)
        self._held[number] = word
        self._last_ok[number] = dt_util.utcnow()
        self._ensure_timer()
        self._notify()

    async def async_release(self, number: int) -> None:
        """Stop asserting a control.

        Releasing writes nothing - the controller undoes it within five minutes
        - because 0 is a documented *command* on several of these registers
        (0 = Off on the zone modes). 1100 is the exception: 0 there is the
        documented "all inputs open", and a SmartGrid block that lingers for
        five minutes after the user cleared it is worse than one free write.
        """
        if number not in self._held:
            return
        if number == VDI_REGISTER:
            await self._write(number, 0)
        self._drop(number)

    async def _write(self, number: int, word: int) -> None:
        try:
            await self._hub.async_write_register(number, word)
        except CtcConnectionError as err:
            raise HomeAssistantError(
                f"Writing control register {number} failed: {err}"
            ) from err

    @callback
    def _drop(self, number: int) -> None:
        self._held.pop(number, None)
        self._last_ok.pop(number, None)
        if not self._held:
            self._stop_timer()
        self._notify()

    # -- the refresh ------------------------------------------------------

    @callback
    def _ensure_timer(self) -> None:
        if self._cancel_timer is None:
            self._cancel_timer = async_track_time_interval(
                self._hass, self._async_refresh, REFRESH
            )

    @callback
    def _stop_timer(self) -> None:
        """No holds, no timer - a config entry that asserts nothing does
        nothing."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def async_shutdown(self) -> None:
        """Stop refreshing. Deliberately writes nothing: whatever was held is
        gone from the controller within five minutes, and a teardown is the
        wrong moment to command a heating system."""
        self._stop_timer()
        self._held.clear()
        self._last_ok.clear()

    async def _async_refresh(self, now: datetime) -> None:
        # `now` rather than utcnow(): it is the tick's own timestamp, which is
        # what "how long since this was last written" should be measured
        # against.
        for number, word in list(self._held.items()):
            try:
                await self._hub.async_write_register(number, word)
            except CtcConnectionError as err:
                if now - self._last_ok[number] >= EXPIRY:
                    _LOGGER.warning(
                        "Control register %s has not been refreshed for %s; "
                        "the controller has expired it (%s)",
                        number,
                        EXPIRY,
                        err,
                    )
                    self._drop(number)
                else:
                    _LOGGER.debug(
                        "Refresh of control register %s failed, will retry "
                        "(%s)",
                        number,
                        err,
                    )
                continue
            self._last_ok[number] = now
