"""``factory product`` commands: the operator side of the product registry (T070, ADR-030).

A product is the top level of factory work (ADR-030 p.1/p.3): registering one is
the operator's first action, before any change. The four commands are thin
wrappers over the same core the API serves (``orchestration.state.change_store``
for the registry, ``orchestration.products`` for readiness), so the CLI and the
Console cannot disagree on what a product is or when it is ready:

* ``product add`` registers a product; a repeat with the same ``--id`` replays the
  existing one and writes nothing (FR-017, ADR-030 p.6);
* ``product validate`` observes the repository through the ``provisioning`` seam
  the composition root binds (``RepositoryProvisioningPort``, ADR-031) and records
  ``validating -> ready | error`` in one transaction;
* ``product list`` and ``product show`` read the registry.

The CLI is core and never imports a port or an adapter: the provisioning port
arrives as a value from ``runtime.entrypoint`` (ADR-024 p.5); without it
``product validate`` refuses instead of inventing a readiness (ADR-031 p.6).
Registration and validation append one audit row each (``product.add``,
``product.validate``) attributed to the CLI actor, like ``run withdraw`` (T064).

Exit codes (contract cli.md):

* ``0`` — ``add``: registered or replayed; ``validate``: the product is ``ready``;
  ``list``/``show``: printed.
* ``1`` — ``validate``: the repository is not available, so ``error`` is recorded
  with its reason; or the observation itself failed and nothing was written.
* ``2`` — invalid input, an unknown product, a missing provisioning port, an
  unreachable or refusing store.

Errors never echo a raw exception or the database URL (ADR-009): a failed
observation is reported with fixed wording, and the store's own messages, which
may embed identifiers, stay out of the output. ``--json`` prints exactly one
document on stdout: ``add`` — ``{"outcome", "persisted", "product"}``; ``validate``
— ``{"outcome", "product", "validation"}``; ``show`` — the ``Product`` document;
``list`` — the array of ``Product`` documents.
"""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from dark_factory.changes.enums import ProductStatus
from dark_factory.changes.product import Product
from dark_factory.changes.refs import RepositoryRef
from dark_factory.cli._common import (
    CLI_ACTOR,
    StateStoreUnreachableError,
    open_state_store,
    report_error,
    report_execution_error,
    report_invalid_input,
    report_state_store_unreachable,
)
from dark_factory.cli.main import (
    EXIT_ERROR,
    EXIT_OK,
    ProductAddArgs,
    ProductListArgs,
    ProductShowArgs,
    ProductValidateArgs,
)
from dark_factory.orchestration.products import (
    PROVISIONING_UNCONFIGURED_DETAIL,
    observe_repository,
    record_validation,
)
from dark_factory.orchestration.state.change_store import (
    PRODUCT_ADD_ACTION,
    PRODUCT_VALIDATE_ACTION,
    AuditRepository,
    ProductRepository,
)
from dark_factory.orchestration.state.engine import session_scope
from dark_factory.orchestration.state.repositories import StateConflictError

if TYPE_CHECKING:
    # Type-only: the CLI is core and must not import the ports package (ADR-024
    # p.5); the port arrives as a value from the composition root.
    from dark_factory.ports import RepositoryProvisioningPort, RepositoryValidation

__all__ = [
    "MAX_LIST_LIMIT",
    "OBSERVATION_FAILED",
    "render_add_json",
    "render_add_text",
    "render_product_text",
    "render_validate_json",
    "render_validate_text",
    "run_product_add_command",
    "run_product_list_command",
    "run_product_show_command",
    "run_product_validate_command",
]

MAX_LIST_LIMIT: Final[int] = 200
"""Largest ``--limit`` of ``product list``: the same page bound as ``GET /products``."""

OBSERVATION_FAILED: Final[str] = (
    "the product repository could not be observed: the provisioning adapter failed;"
    " the product status is unchanged"
)
"""Fixed diagnostic of a failed observation; the adapter's text is never echoed (ADR-009)."""

INVALID_PRODUCT_FIELDS: Final[str] = (
    "the product fields are invalid: --id, --name and --repository must be non-blank"
)
"""Fixed diagnostic of a request the domain model refuses (exit 2, nothing ran)."""


class _ObservationFailed(RuntimeError):
    """The provisioning adapter raised while observing; carried out of the transaction."""


# --- commands ---------------------------------------------------------------


def run_product_add_command(
    args: ProductAddArgs, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Handle ``factory product add``; return the process exit code (T070, ADR-030 p.1).

    The product is built from the flags before the store is touched, so a request
    the domain refuses is exit 2 without a transaction. Registration is idempotent
    by id (FR-017): an existing product is replayed, nothing is written and the
    audit row says so. ``session_factory`` is the injection seam for tests.
    """
    try:
        product = Product(
            id=args.product_id,
            name=args.name,
            description=args.description,
            repository=RepositoryRef(provider=args.provider, slug=args.slug),
            repository_url=args.repository_url,
            baseline_ref=args.baseline_ref,
            dev_env_ref=args.dev_env_ref,
        )
    except ValidationError:
        return report_invalid_input(
            "product add", INVALID_PRODUCT_FIELDS, json_output=args.json_output
        )
    return _with_store(
        "product add",
        args.json_output,
        session_factory,
        lambda factory: _add(factory, args, product),
    )


def run_product_validate_command(
    args: ProductValidateArgs,
    *,
    provisioning: "RepositoryProvisioningPort | None" = None,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Handle ``factory product validate``; return the process exit code (T070, ADR-031).

    Without a ``provisioning`` port the command refuses with exit 2 before any
    read: a readiness the factory did not observe is never reported (ADR-031
    p.6). Otherwise the product is read, its repository observed and the outcome
    recorded in one transaction — ``ready`` is exit 0, ``error`` (the repository
    is not available) is exit 1 with the reason recorded and printed. A failure
    of the observation itself is exit 1 with fixed wording, and nothing is written.
    """
    if provisioning is None:
        return report_invalid_input(
            "product validate", PROVISIONING_UNCONFIGURED_DETAIL, json_output=args.json_output
        )
    return _with_store(
        "product validate",
        args.json_output,
        session_factory,
        lambda factory: _validate(factory, args, provisioning),
    )


def run_product_list_command(
    args: ProductListArgs, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Handle ``factory product list``; return the process exit code (T070)."""
    if not 1 <= args.limit <= MAX_LIST_LIMIT or args.offset < 0:
        return report_invalid_input(
            "product list",
            f"--limit must be within 1..{MAX_LIST_LIMIT} and --offset must be >= 0",
            json_output=args.json_output,
        )
    return _with_store(
        "product list", args.json_output, session_factory, lambda factory: _list(factory, args)
    )


def run_product_show_command(
    args: ProductShowArgs, *, session_factory: sessionmaker[Session] | None = None
) -> int:
    """Handle ``factory product show``; return the process exit code (T070)."""
    return _with_store(
        "product show", args.json_output, session_factory, lambda factory: _show(factory, args)
    )


# --- store access -----------------------------------------------------------


def _with_store(
    command: str,
    json_output: bool,
    session_factory: sessionmaker[Session] | None,
    action: Callable[[sessionmaker[Session]], int],
) -> int:
    """Run ``action`` over the injected store, or over one opened for this command.

    The store is probed up front (``open_state_store``), so an unreachable or
    misconfigured database is exit 2 with fixed wording instead of a traceback
    that could embed the URL (ADR-009).
    """
    if session_factory is not None:
        return action(session_factory)
    try:
        with open_state_store() as factory:
            return action(factory)
    except StateStoreUnreachableError:
        return report_state_store_unreachable(command, json_output=json_output)


def _audit(session: Session, action: str, product_id: str, outcome: str) -> None:
    """Append the operator decision to the audit log inside the caller's transaction."""
    AuditRepository(session).append(
        actor=CLI_ACTOR,
        role=None,
        action=action,
        resource_type="product",
        resource_id=product_id,
        outcome=outcome,
    )


def _add(factory: sessionmaker[Session], args: ProductAddArgs, product: Product) -> int:
    try:
        with session_scope(factory) as session:
            stored, created = ProductRepository(session).create(product)
            _audit(session, PRODUCT_ADD_ACTION, stored.id, "created" if created else "replayed")
    except SQLAlchemyError:
        return report_state_store_unreachable("product add", json_output=args.json_output)
    print(
        render_add_json(stored, created=created)
        if args.json_output
        else render_add_text(stored, created=created)
    )
    return EXIT_OK


def _validate(
    factory: sessionmaker[Session],
    args: ProductValidateArgs,
    provisioning: "RepositoryProvisioningPort",
) -> int:
    try:
        with session_scope(factory) as session:
            repository = ProductRepository(session)
            product = repository.get(args.product_id)
            if product is None:
                return _unknown_product("product validate", args.product_id, args.json_output)
            try:
                validation = observe_repository(provisioning, product)
            except Exception as error:
                # Any adapter failure — credentials, network, a refused clone — is
                # an execution error of this command, not a fact about the
                # repository: no status is recorded and the text is not echoed.
                raise _ObservationFailed() from error
            product = record_validation(repository, product, validation)
            _audit(session, PRODUCT_VALIDATE_ACTION, product.id, "created")
    except _ObservationFailed:
        return report_execution_error(
            "product validate", OBSERVATION_FAILED, json_output=args.json_output
        )
    except StateConflictError:
        # The store refused the transition (a concurrent update moved the
        # revision): nothing was recorded. The text may embed identifiers.
        return report_invalid_input(
            "product validate",
            "the state store refused the transition (concurrent update); nothing was recorded",
            json_output=args.json_output,
        )
    except SQLAlchemyError:
        return report_state_store_unreachable("product validate", json_output=args.json_output)
    print(
        render_validate_json(product, validation)
        if args.json_output
        else render_validate_text(product, validation)
    )
    return EXIT_OK if product.status is ProductStatus.READY else EXIT_ERROR


def _list(factory: sessionmaker[Session], args: ProductListArgs) -> int:
    try:
        with session_scope(factory) as session:
            products = ProductRepository(session).list(limit=args.limit, offset=args.offset)
    except SQLAlchemyError:
        return report_state_store_unreachable("product list", json_output=args.json_output)
    if args.json_output:
        print(json.dumps([product.model_dump(mode="json") for product in products]))
    elif products:
        print("\n".join(_headline(product) for product in products))
    else:
        print("no products registered")
    return EXIT_OK


def _show(factory: sessionmaker[Session], args: ProductShowArgs) -> int:
    try:
        with session_scope(factory) as session:
            product = ProductRepository(session).get(args.product_id)
    except SQLAlchemyError:
        return report_state_store_unreachable("product show", json_output=args.json_output)
    if product is None:
        return _unknown_product("product show", args.product_id, args.json_output)
    print(
        json.dumps(product.model_dump(mode="json"))
        if args.json_output
        else render_product_text(product)
    )
    return EXIT_OK


def _unknown_product(command: str, product_id: str, json_output: bool) -> int:
    """An unknown product is invalid input (exit 2); the id is the operator's own."""
    return report_error(
        command, "invalid_input", f"unknown product {product_id!r}", 2, json_output=json_output
    )


# --- rendering --------------------------------------------------------------


def _headline(product: Product) -> str:
    """One line per product: id, readiness and the facts that identify it."""
    return (
        f"product {product.id}: {product.status.value}"
        f" (name={product.name}, repository={product.repository.provider.value}:"
        f"{product.repository.slug}, state_revision={product.state_revision})"
    )


def render_product_text(product: Product) -> str:
    """Human-readable product report: the headline plus one line per optional fact."""
    lines = [_headline(product)]
    if product.status_reason is not None:
        lines.append(f"reason: {product.status_reason}")
    for label, value in (
        ("description", product.description),
        ("repository_url", product.repository_url),
        ("baseline_ref", product.baseline_ref),
        ("dev_env_ref", product.dev_env_ref),
    ):
        if value is not None:
            lines.append(f"{label}: {value}")
    lines.append(f"created_at: {product.created_at.isoformat()}")
    return "\n".join(lines)


def render_add_text(product: Product, *, created: bool) -> str:
    """Registration report: registered, or replayed with nothing written."""
    if created:
        return (
            f"product {product.id}: registered (status={product.status.value},"
            f" repository={product.repository.provider.value}:{product.repository.slug},"
            f" state_revision={product.state_revision})"
        )
    return (
        f"product {product.id}: replayed (status={product.status.value};"
        " the product was already registered, nothing was written)"
    )


def render_add_json(product: Product, *, created: bool) -> str:
    """Stable JSON of one registration (shape in the module docstring)."""
    payload: dict[str, object] = {
        "outcome": "created" if created else "replayed",
        "persisted": created,
        "product": product.model_dump(mode="json"),
    }
    return json.dumps(payload)


def render_validate_text(product: Product, validation: "RepositoryValidation") -> str:
    """Validation report: the recorded readiness and the observation behind it."""
    lines = [
        f"product {product.id}: {product.status.value}"
        f" (state={validation.state.value}, default_branch={validation.default_branch},"
        f" head_revision={validation.head_revision}, state_revision={product.state_revision})"
    ]
    if product.status_reason is not None:
        lines.append(f"reason: {product.status_reason}")
    return "\n".join(lines)


def render_validate_json(product: Product, validation: "RepositoryValidation") -> str:
    """Stable JSON of one validation (shape in the module docstring)."""
    payload: dict[str, object] = {
        "outcome": product.status.value,
        "product": product.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
    }
    return json.dumps(payload)
