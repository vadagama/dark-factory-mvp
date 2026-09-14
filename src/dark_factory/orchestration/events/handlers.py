"""Consumer-side delivery handlers (T028, ADR-016 p.2).

A :class:`DeliveryHandler` is the SPI a consumer implements to receive outbox
events. This is *not* a port of an external system (no change in
``dark_factory.ports``): it wires the dispatcher to internal consumers — the
tracker/status publisher (T-063), the console tracker and later CI-pipeline
consumers. The :class:`HandlerRegistry` is the extension point: register real
handlers per ``consumer_id``; unknown consumers fall back to
:class:`NoOpHandler` so an unregistered consumer never breaks a pass.

Contract (ADR-016 p.2): delivery is at-least-once — exactly-once is not
promised. Handlers MUST deduplicate by ``event_id`` (a redelivery after a
crash between the handler call and the outcome commit must not create a
second effect; the effect ledger, ADR-006 p.3, gives internal consumers the
marker to do it). Handlers are called *outside* any transaction: the
dispatcher commits the reservation before calling and records the outcome in
a separate transaction afterwards, so a slow handler never holds a database
transaction open.
"""

import logging
from typing import Protocol, runtime_checkable

from dark_factory.ports import DomainEvent

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class DeliveryHandler(Protocol):
    """Delivery SPI of one consumer: perform the effect for ``event`` or raise."""

    async def deliver(self, event: DomainEvent, consumer_id: str, /) -> None: ...


class NoOpHandler:
    """Log-only handler for consumers without wiring yet (MVP default).

    Real consumers (T-063/T-090, console tracker) replace it in the registry;
    delivery stays observable through the ``event_delivery`` state either way.
    """

    async def deliver(self, event: DomainEvent, consumer_id: str, /) -> None:
        _LOGGER.debug(
            "outbox: no-op delivery of %s to %r (no handler registered)",
            event.event_id,
            consumer_id,
        )


class HandlerRegistry:
    """Consumer id → handler mapping with a safe fallback."""

    def __init__(self) -> None:
        self._handlers: dict[str, DeliveryHandler] = {}
        self._fallback = NoOpHandler()

    def register(self, consumer_id: str, handler: DeliveryHandler) -> None:
        """Bind ``handler`` to ``consumer_id`` (last registration wins)."""
        self._handlers[consumer_id] = handler

    def handler_for(self, consumer_id: str) -> DeliveryHandler:
        """The handler of ``consumer_id``; unknown consumers get the no-op fallback."""
        return self._handlers.get(consumer_id, self._fallback)
