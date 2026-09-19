"""Product aggregate: defaults, status walk, round-trips and backward compatibility (T065).

No database is involved: these are the domain invariants of ADR-030 p.1/p.2.
"""

import pytest

from dark_factory.changes import (
    Change,
    InvalidStatusTransition,
    Product,
    ProductStatus,
    Provider,
    from_json,
    from_yaml,
    to_json,
    to_yaml,
)
from tests.changes_factories import NOW, make_change, make_product


def test_product_defaults() -> None:
    product = make_product()
    assert product.status is ProductStatus.CREATED
    assert product.status_reason is None
    assert product.state_revision == 1
    assert product.repository.provider is Provider.GITHUB
    assert product.created_at == NOW


def test_product_optional_fields_default_to_none() -> None:
    product = Product(id="prd-1", name="Pilot", repository=make_product().repository)
    assert product.description is None
    assert product.repository_url is None
    assert product.baseline_ref is None
    assert product.dev_env_ref is None
    assert product.status_reason is None
    assert product.created_at.tzinfo is not None


def test_product_round_trips_through_json() -> None:
    product = make_product()
    restored = from_json(Product, to_json(product))
    assert restored == product
    assert restored.repository.provider is Provider.GITHUB
    assert restored.status is ProductStatus.CREATED


def test_product_round_trips_through_yaml() -> None:
    assert from_yaml(Product, to_yaml(make_product())) == make_product()


def test_product_status_walks_created_to_ready() -> None:
    product = make_product()
    product.apply_status(ProductStatus.VALIDATING)
    assert product.status.value == ProductStatus.VALIDATING.value
    assert product.state_revision == 2
    product.apply_status(ProductStatus.READY)
    assert product.status is ProductStatus.READY
    assert product.status_reason is None
    assert product.state_revision == 3


def test_product_status_records_the_error_reason() -> None:
    product = make_product()
    product.apply_status(ProductStatus.VALIDATING)
    product.apply_status(ProductStatus.ERROR, reason="repository is not reachable")
    assert product.status is ProductStatus.ERROR
    assert product.status_reason == "repository is not reachable"


def test_product_rejects_a_transition_outside_the_table() -> None:
    product = make_product()
    with pytest.raises(InvalidStatusTransition):
        product.apply_status(ProductStatus.READY)  # created -> ready
    product.apply_status(ProductStatus.VALIDATING)
    product.apply_status(ProductStatus.READY)
    with pytest.raises(InvalidStatusTransition):
        product.apply_status(ProductStatus.VALIDATING)  # ready is terminal


def test_error_requires_a_reason_and_other_statuses_reject_one() -> None:
    product = make_product()
    product.apply_status(ProductStatus.VALIDATING)
    with pytest.raises(ValueError, match="requires a reason"):
        product.apply_status(ProductStatus.ERROR)
    with pytest.raises(ValueError, match="only allowed for"):
        product.apply_status(ProductStatus.READY, reason="nope")
    assert product.status is ProductStatus.VALIDATING
    assert product.state_revision == 2


def test_a_change_payload_without_product_id_still_validates() -> None:
    """Pre-T065 intake payloads carry no ``product_id`` (ADR-030 p.2)."""
    payload = make_change().model_dump(mode="json")
    payload.pop("product_id", None)
    change = Change.model_validate(payload)
    assert change.product_id is None


def test_change_carries_an_optional_product_id() -> None:
    payload = {**make_change().model_dump(mode="json"), "product_id": "prd-001"}
    change = Change.model_validate(payload)
    assert change.product_id == "prd-001"
    assert from_json(Change, to_json(change)).product_id == "prd-001"
