"""In-memory fake of the event publisher port (ADR-016, contracts/events.md)."""

from dark_factory.ports import DomainEvent, EventPublisherPort


class FakeEventPublisher(EventPublisherPort):
    """In-memory ``EventPublisherPort`` deduplicating by ``event_id``.

    Consumers deduplicate by ``eventId`` (contracts/events.md); the fake applies
    the same rule on the publishing side, so a replay never creates a duplicate.
    """

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []
        self._seen: set[str] = set()

    async def publish(self, event: DomainEvent, /) -> None:
        if event.event_id in self._seen:
            return
        self._seen.add(event.event_id)
        self._events.append(event)

    def recorded_events(self) -> tuple[DomainEvent, ...]:
        """Read view of accepted events (not part of the port)."""
        return tuple(self._events)
