"""``factory product`` commands over a stubbed registry (T070, ADR-030, contract cli.md).

The store seams (``ProductRepository``/``AuditRepository``) are replaced by
in-memory stubs, so these tests hold the command contract — argument handling,
outcome-to-exit-code mapping, text and ``--json`` rendering, the audit rows an
operator decision leaves and the ADR-009 hygiene of every error path — without a
database. The PostgreSQL-backed pass lives in
``tests/integration/test_cli_products.py``.
"""

import json
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import dark_factory.cli.products as products_module
from dark_factory.changes.enums import ProductStatus, Provider
from dark_factory.changes.product import Product
from dark_factory.changes.refs import RepositoryRef
from dark_factory.cli._common import CLI_ACTOR, StateStoreUnreachableError
from dark_factory.cli.main import (
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    ProductAddArgs,
    ProductListArgs,
    ProductShowArgs,
    ProductValidateArgs,
)
from dark_factory.orchestration.products import PROVISIONING_UNCONFIGURED_DETAIL
from dark_factory.orchestration.state.change_store import (
    PRODUCT_ADD_ACTION,
    PRODUCT_VALIDATE_ACTION,
)
from dark_factory.orchestration.state.repositories import StateConflictError
from dark_factory.ports import RepositoryState, RepositoryValidation

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
PRODUCT_ID = "prd-calc"
REPOSITORY = RepositoryRef(provider=Provider.GITHUB, slug="small/calculator")


# --- stubs -------------------------------------------------------------------


class StubSession:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class StubSessionFactory:
    def __call__(self) -> StubSession:
        return StubSession()


def _factory() -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", StubSessionFactory())


class StubProductStore:
    """In-memory ``ProductRepository``: the same contract, no database."""

    products: ClassVar[dict[str, Product]] = {}
    error: ClassVar[Exception | None] = None
    instances: ClassVar[int] = 0

    def __init__(self, session: StubSession) -> None:
        StubProductStore.instances += 1

    def _raise(self) -> None:
        if StubProductStore.error is not None:
            raise StubProductStore.error

    def create(self, product: Product) -> tuple[Product, bool]:
        self._raise()
        existing = StubProductStore.products.get(product.id)
        if existing is not None:
            return existing.model_copy(deep=True), False
        StubProductStore.products[product.id] = product.model_copy(deep=True)
        return product, True

    def get(self, product_id: str) -> Product | None:
        self._raise()
        product = StubProductStore.products.get(product_id)
        return product.model_copy(deep=True) if product is not None else None

    def list(self, *, limit: int = 50, offset: int = 0) -> list[Product]:
        self._raise()
        ordered = sorted(StubProductStore.products.values(), key=lambda p: (p.created_at, p.id))
        return [product.model_copy(deep=True) for product in ordered[offset : offset + limit]]

    def update_status(
        self,
        product_id: str,
        target: ProductStatus,
        *,
        expected_revision: int,
        reason: str | None = None,
    ) -> Product:
        self._raise()
        product = StubProductStore.products[product_id]
        if product.state_revision != expected_revision:
            raise StateConflictError(
                f"product {product_id!r} is at revision {product.state_revision}"
            )
        product.apply_status(target, reason=reason)
        return product.model_copy(deep=True)


class StubAudit:
    rows: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, session: StubSession) -> None: ...

    def append(self, **kwargs: Any) -> None:
        StubAudit.rows.append(kwargs)


class StubProvisioning:
    """Provisioning port stand-in: one seeded state, or one failure."""

    def __init__(self, state: RepositoryState, *, error: Exception | None = None) -> None:
        self.state = state
        self.error = error
        self.calls: list[RepositoryRef] = []

    async def validate(self, repository: RepositoryRef, /) -> RepositoryValidation:
        self.calls.append(repository)
        if self.error is not None:
            raise self.error
        observable = self.state is not RepositoryState.UNAVAILABLE
        return RepositoryValidation(
            repository=repository,
            state=self.state,
            default_branch="main" if observable else None,
            head_revision=(
                "abc123"
                if self.state not in {RepositoryState.UNAVAILABLE, RepositoryState.EMPTY}
                else None
            ),
        )

    async def ensure_mirror(self, repository: RepositoryRef, /, **kwargs: Any) -> Any:
        raise AssertionError("validation never prepares a mirror")

    async def bootstrap_baseline(self, repository: RepositoryRef, /, **kwargs: Any) -> Any:
        raise AssertionError("validation never bootstraps a baseline")


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch: pytest.MonkeyPatch) -> None:
    StubProductStore.products = {}
    StubProductStore.error = None
    StubProductStore.instances = 0
    StubAudit.rows = []
    monkeypatch.setattr(products_module, "ProductRepository", StubProductStore)
    monkeypatch.setattr(products_module, "AuditRepository", StubAudit)


def _seed(status: ProductStatus = ProductStatus.CREATED, reason: str | None = None) -> Product:
    product = Product(
        id=PRODUCT_ID,
        name="Calculator",
        description="the pilot calculator",
        repository=REPOSITORY,
        repository_url="https://github.com/small/calculator",
        baseline_ref=".factory/product",
        dev_env_ref="apps-dev/calculator",
        created_at=NOW,
    )
    if status is not ProductStatus.CREATED:
        product.apply_status(ProductStatus.VALIDATING)
        product.apply_status(status, reason=reason)
    StubProductStore.products[product.id] = product
    return product


def _add_args(**overrides: Any) -> ProductAddArgs:
    fields: dict[str, Any] = {
        "product_id": PRODUCT_ID,
        "name": "Calculator",
        "provider": Provider.GITHUB,
        "slug": "small/calculator",
        "description": None,
        "repository_url": None,
        "baseline_ref": None,
        "dev_env_ref": None,
        "json_output": False,
    }
    fields.update(overrides)
    return ProductAddArgs(**fields)


# --- product add --------------------------------------------------------------


def test_add_registers_the_product_and_records_the_decision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = products_module.run_product_add_command(_add_args(), session_factory=_factory())

    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == (
        "product prd-calc: registered (status=created, repository=github:small/calculator,"
        " state_revision=1)"
    )
    stored = StubProductStore.products[PRODUCT_ID]
    assert stored.repository == REPOSITORY
    assert stored.status is ProductStatus.CREATED
    assert StubAudit.rows == [
        {
            "actor": CLI_ACTOR,
            "role": None,
            "action": PRODUCT_ADD_ACTION,
            "resource_type": "product",
            "resource_id": PRODUCT_ID,
            "outcome": "created",
        }
    ]


def test_add_json_reports_the_registration(capsys: pytest.CaptureFixture[str]) -> None:
    code = products_module.run_product_add_command(
        _add_args(json_output=True, description="calc", baseline_ref=".factory/product"),
        session_factory=_factory(),
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "created"
    assert payload["persisted"] is True
    assert payload["product"]["id"] == PRODUCT_ID
    assert payload["product"]["description"] == "calc"
    assert payload["product"]["baseline_ref"] == ".factory/product"
    assert payload["product"]["repository"] == {"provider": "github", "slug": "small/calculator"}
    assert payload["product"]["status"] == "created"


def test_add_replays_an_existing_id_and_writes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    existing = _seed(ProductStatus.READY)

    code = products_module.run_product_add_command(
        _add_args(name="Another name", json_output=True), session_factory=_factory()
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "replayed"
    assert payload["persisted"] is False
    assert payload["product"]["name"] == existing.name, "the request never overwrites"
    assert payload["product"]["status"] == "ready"
    assert StubProductStore.products[PRODUCT_ID].name == existing.name
    assert [row["outcome"] for row in StubAudit.rows] == ["replayed"]


def test_add_replay_text_says_nothing_was_written(capsys: pytest.CaptureFixture[str]) -> None:
    _seed()

    products_module.run_product_add_command(_add_args(), session_factory=_factory())

    assert capsys.readouterr().out.strip() == (
        "product prd-calc: replayed (status=created;"
        " the product was already registered, nothing was written)"
    )


def test_add_with_a_blank_repository_is_invalid_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = products_module.run_product_add_command(
        _add_args(slug="", json_output=True), session_factory=_factory()
    )

    assert code == EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_input"
    assert StubProductStore.instances == 0, "the store is not touched"
    assert StubAudit.rows == []


# --- product list / show -------------------------------------------------------


def test_list_prints_one_line_per_product(capsys: pytest.CaptureFixture[str]) -> None:
    _seed(ProductStatus.READY)
    StubProductStore.products["prd-other"] = Product(
        id="prd-other",
        name="Other",
        repository=RepositoryRef(provider=Provider.GITLAB, slug="team/other"),
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
    )

    code = products_module.run_product_list_command(
        ProductListArgs(limit=50, offset=0, json_output=False), session_factory=_factory()
    )

    assert code == EXIT_OK
    assert capsys.readouterr().out.strip().splitlines() == [
        "product prd-calc: ready (name=Calculator, repository=github:small/calculator,"
        " state_revision=3)",
        "product prd-other: created (name=Other, repository=gitlab:team/other, state_revision=1)",
    ]


def test_list_json_is_the_array_of_product_documents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed()

    code = products_module.run_product_list_command(
        ProductListArgs(limit=50, offset=0, json_output=True), session_factory=_factory()
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert [product["id"] for product in payload] == [PRODUCT_ID]
    assert payload[0]["status"] == "created"


def test_list_without_products_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    code = products_module.run_product_list_command(
        ProductListArgs(limit=50, offset=0, json_output=False), session_factory=_factory()
    )

    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == "no products registered"


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (201, 0), (10, -1)])
def test_list_rejects_a_page_outside_the_api_bounds(
    limit: int, offset: int, capsys: pytest.CaptureFixture[str]
) -> None:
    code = products_module.run_product_list_command(
        ProductListArgs(limit=limit, offset=offset, json_output=False),
        session_factory=_factory(),
    )

    assert code == EXIT_INVALID_INPUT
    assert "factory product list:" in capsys.readouterr().err
    assert StubProductStore.instances == 0


def test_show_prints_the_product_and_its_readiness(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(ProductStatus.ERROR, reason="the repository is not available to the factory")

    code = products_module.run_product_show_command(
        ProductShowArgs(product_id=PRODUCT_ID, json_output=False), session_factory=_factory()
    )

    assert code == EXIT_OK
    assert capsys.readouterr().out.strip().splitlines() == [
        "product prd-calc: error (name=Calculator, repository=github:small/calculator,"
        " state_revision=3)",
        "reason: the repository is not available to the factory",
        "description: the pilot calculator",
        "repository_url: https://github.com/small/calculator",
        "baseline_ref: .factory/product",
        "dev_env_ref: apps-dev/calculator",
        "created_at: 2026-09-19T12:00:00+00:00",
    ]


def test_show_json_is_the_product_document(capsys: pytest.CaptureFixture[str]) -> None:
    _seed(ProductStatus.READY)

    code = products_module.run_product_show_command(
        ProductShowArgs(product_id=PRODUCT_ID, json_output=True), session_factory=_factory()
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == PRODUCT_ID
    assert payload["status"] == "ready"
    assert payload["state_revision"] == 3


def test_show_of_an_unknown_product_is_invalid_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = products_module.run_product_show_command(
        ProductShowArgs(product_id="prd-missing", json_output=True), session_factory=_factory()
    )

    assert code == EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_input",
        "detail": "unknown product 'prd-missing'",
    }


# --- product validate ------------------------------------------------------------


def test_validate_records_readiness_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    _seed()
    provisioning = StubProvisioning(RepositoryState.BASELINE_ABSENT)

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=False),
        provisioning=provisioning,
        session_factory=_factory(),
    )

    assert code == EXIT_OK
    assert capsys.readouterr().out.strip() == (
        "product prd-calc: ready (state=baseline_absent, default_branch=main,"
        " head_revision=abc123, state_revision=3)"
    )
    assert provisioning.calls == [REPOSITORY]
    stored = StubProductStore.products[PRODUCT_ID]
    assert stored.status is ProductStatus.READY
    assert stored.status_reason is None
    # created -> validating -> ready: the state machine passes through validating.
    assert stored.state_revision == 3
    assert StubAudit.rows == [
        {
            "actor": CLI_ACTOR,
            "role": None,
            "action": PRODUCT_VALIDATE_ACTION,
            "resource_type": "product",
            "resource_id": PRODUCT_ID,
            "outcome": "created",
        }
    ]


def test_validate_json_carries_the_product_and_the_observation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed()

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
        provisioning=StubProvisioning(RepositoryState.EMPTY),
        session_factory=_factory(),
    )

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "ready"
    assert payload["product"]["status"] == "ready"
    assert payload["product"]["state_revision"] == 3
    assert payload["validation"] == {
        "repository": {"provider": "github", "slug": "small/calculator"},
        "state": "empty",
        "default_branch": "main",
        "head_revision": None,
    }


def test_validate_of_an_unavailable_repository_records_error_and_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed()

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=False),
        provisioning=StubProvisioning(RepositoryState.UNAVAILABLE),
        session_factory=_factory(),
    )

    assert code == EXIT_ERROR
    assert capsys.readouterr().out.strip().splitlines() == [
        "product prd-calc: error (state=unavailable, default_branch=None,"
        " head_revision=None, state_revision=3)",
        "reason: the repository is not available to the factory",
    ]
    stored = StubProductStore.products[PRODUCT_ID]
    assert stored.status is ProductStatus.ERROR
    assert stored.status_reason == "the repository is not available to the factory"
    assert [row["action"] for row in StubAudit.rows] == [PRODUCT_VALIDATE_ACTION]


def test_validate_can_be_repeated_on_a_ready_product(capsys: pytest.CaptureFixture[str]) -> None:
    _seed(ProductStatus.READY)

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
        provisioning=StubProvisioning(RepositoryState.BASELINE_CURRENT),
        session_factory=_factory(),
    )

    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["product"]["state_revision"] == 5


def test_validate_without_a_provisioning_port_refuses_before_any_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed()

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
        provisioning=None,
        session_factory=_factory(),
    )

    assert code == EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_input",
        "detail": PROVISIONING_UNCONFIGURED_DETAIL,
    }
    assert StubProductStore.instances == 0, "no store access without a port"
    assert StubProductStore.products[PRODUCT_ID].status is ProductStatus.CREATED
    assert StubAudit.rows == []


def test_validate_of_an_unknown_product_is_invalid_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    provisioning = StubProvisioning(RepositoryState.EMPTY)

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id="prd-missing", json_output=False),
        provisioning=provisioning,
        session_factory=_factory(),
    )

    assert code == EXIT_INVALID_INPUT
    assert capsys.readouterr().err.strip() == (
        "factory product validate: unknown product 'prd-missing'"
    )
    assert provisioning.calls == [], "nothing is observed for a product that does not exist"
    assert StubAudit.rows == []


def test_a_failed_observation_is_an_execution_error_without_echo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed()
    provisioning = StubProvisioning(
        RepositoryState.EMPTY, error=RuntimeError("clone failed: token ghp_topsecret rejected")
    )

    code = products_module.run_product_validate_command(
        ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
        provisioning=provisioning,
        session_factory=_factory(),
    )

    assert code == EXIT_ERROR
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "error": "execution_error",
        "detail": products_module.OBSERVATION_FAILED,
    }
    assert "ghp_topsecret" not in captured.out + captured.err
    assert StubProductStore.products[PRODUCT_ID].status is ProductStatus.CREATED
    assert StubAudit.rows == [], "a failed observation records nothing"


def test_a_concurrent_transition_is_reported_without_a_write(
    capsys: pytest.CaptureFixture[str],
) -> None:
    product = _seed()

    class RacingStore(StubProductStore):
        def get(self, product_id: str) -> Product | None:
            stale = super().get(product_id)
            assert stale is not None
            # Someone else moved the product after our read.
            StubProductStore.products[product_id].apply_status(ProductStatus.VALIDATING)
            return stale

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(products_module, "ProductRepository", RacingStore)
        code = products_module.run_product_validate_command(
            ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
            provisioning=StubProvisioning(RepositoryState.EMPTY),
            session_factory=_factory(),
        )

    assert code == EXIT_INVALID_INPUT
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_input"
    assert "concurrent update" in payload["detail"]
    assert product.id not in payload["detail"], "the store's own text is not echoed"
    assert StubAudit.rows == []


# --- store failures (ADR-009 hygiene) ----------------------------------------------


def _commands() -> list[tuple[str, Any]]:
    return [
        (
            "product add",
            lambda factory: products_module.run_product_add_command(
                _add_args(json_output=True), session_factory=factory
            ),
        ),
        (
            "product validate",
            lambda factory: products_module.run_product_validate_command(
                ProductValidateArgs(product_id=PRODUCT_ID, json_output=True),
                provisioning=StubProvisioning(RepositoryState.EMPTY),
                session_factory=factory,
            ),
        ),
        (
            "product list",
            lambda factory: products_module.run_product_list_command(
                ProductListArgs(limit=50, offset=0, json_output=True), session_factory=factory
            ),
        ),
        (
            "product show",
            lambda factory: products_module.run_product_show_command(
                ProductShowArgs(product_id=PRODUCT_ID, json_output=True), session_factory=factory
            ),
        ),
    ]


@pytest.mark.parametrize(("command", "run"), _commands(), ids=lambda value: str(value))
def test_a_refusing_store_is_exit_2_without_the_url(
    command: str, run: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed()
    StubProductStore.error = SQLAlchemyError("postgresql://user:hunter2@db/factory refused")

    code = run(_factory())

    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "error": "invalid_input",
        "detail": "the state store is not reachable or misconfigured",
    }
    assert "hunter2" not in captured.out + captured.err


@pytest.mark.parametrize(("command", "run"), _commands(), ids=lambda value: str(value))
def test_an_unreachable_store_is_exit_2(
    command: str, run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _unreachable() -> Any:
        raise StateStoreUnreachableError("postgresql://user:hunter2@db/factory is down")

    monkeypatch.setattr(products_module, "open_state_store", _unreachable)

    code = run(None)

    assert code == EXIT_INVALID_INPUT
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"] == "invalid_input"
    assert "hunter2" not in captured.out + captured.err
