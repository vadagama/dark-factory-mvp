"""The core readiness rule the API and the CLI share (T066/T070, ADR-030 p.4, ADR-031)."""

from typing import Any, cast

import pytest

from dark_factory.changes.enums import ProductStatus
from dark_factory.changes.product import Product
from dark_factory.orchestration.products import (
    UNAVAILABLE_REASON,
    observe_repository,
    product_readiness,
    record_validation,
)
from dark_factory.orchestration.state.change_store import ProductRepository
from dark_factory.orchestration.state.repositories import StateConflictError
from dark_factory.ports import RepositoryRef, RepositoryState, RepositoryValidation
from tests.changes_factories import make_product


def _validation(state: RepositoryState) -> RepositoryValidation:
    return RepositoryValidation(repository=make_product().repository, state=state)


@pytest.mark.parametrize("state", list(RepositoryState))
def test_readiness_is_exhaustive_over_the_repository_states(state: RepositoryState) -> None:
    status, reason = product_readiness(_validation(state))
    if state is RepositoryState.UNAVAILABLE:
        assert (status, reason) == (ProductStatus.ERROR, UNAVAILABLE_REASON)
    else:
        # Empty, baseline absent/current/stale: the factory reached the repository.
        assert (status, reason) == (ProductStatus.READY, None)


class RecordingRepository:
    """``ProductRepository`` stand-in: applies the domain transition table in memory."""

    def __init__(self, product: Product) -> None:
        self.product = product
        self.calls: list[tuple[ProductStatus, int, str | None]] = []

    def update_status(
        self,
        product_id: str,
        target: ProductStatus,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Product:
        assert product_id == self.product.id
        self.calls.append((target, expected_revision, reason))
        if self.product.state_revision != expected_revision:
            raise StateConflictError("stale revision")
        self.product.apply_status(target, reason=reason)
        return self.product.model_copy(deep=True)


def _as_store(repository: RecordingRepository) -> ProductRepository:
    """The rule only calls ``update_status``; the stand-in is typed as the real store."""
    return cast("ProductRepository", repository)


def test_record_validation_passes_through_validating() -> None:
    repository = RecordingRepository(make_product())

    product = record_validation(
        _as_store(repository), make_product(), _validation(RepositoryState.BASELINE_ABSENT)
    )

    assert repository.calls == [
        (ProductStatus.VALIDATING, 1, None),
        (ProductStatus.READY, 2, None),
    ]
    assert product.status is ProductStatus.READY
    assert product.state_revision == 3


def test_record_validation_continues_from_validating() -> None:
    stored = make_product()
    stored.apply_status(ProductStatus.VALIDATING)
    repository = RecordingRepository(stored)

    product = record_validation(
        _as_store(repository),
        stored.model_copy(deep=True),
        _validation(RepositoryState.UNAVAILABLE),
    )

    assert repository.calls == [(ProductStatus.ERROR, 2, UNAVAILABLE_REASON)]
    assert product.status is ProductStatus.ERROR
    assert product.status_reason == UNAVAILABLE_REASON


def test_record_validation_surfaces_a_concurrent_transition() -> None:
    stored = make_product()
    stored.apply_status(ProductStatus.VALIDATING)  # moved after the caller's read
    repository = RecordingRepository(stored)

    with pytest.raises(StateConflictError):
        record_validation(_as_store(repository), make_product(), _validation(RepositoryState.EMPTY))


def test_observe_repository_asks_the_port_about_the_product_repository() -> None:
    class Port:
        def __init__(self) -> None:
            self.calls: list[RepositoryRef] = []

        async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation:
            self.calls.append(repository)
            return RepositoryValidation(repository=repository, state=RepositoryState.EMPTY)

        async def ensure_mirror(self, repository: RepositoryRef, /, **kwargs: Any) -> Any:
            raise AssertionError("validation never touches the mirror")

        async def bootstrap_baseline(self, repository: RepositoryRef, /, **kwargs: Any) -> Any:
            raise AssertionError("validation never bootstraps")

    port = Port()
    product = make_product()

    validation = observe_repository(port, product)

    assert port.calls == [product.repository]
    assert validation.state is RepositoryState.EMPTY
