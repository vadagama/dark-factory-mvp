#!/usr/bin/env python3
"""Pilot helper for the factory HTTP API (T-043 increment 1).

Operator tooling for the pilot run: intake of the task matrix, change/run
status, and filing operator decisions. Secrets are read from the environment
only (``DARK_FACTORY_API_TOKENS``) and are never printed, logged or written.

Subcommands:
    intake TASKS_JSON        POST every task of the matrix (dedup-safe).
    status [CHANGE_ID]       Compact view of changes / one change with runs.
    approve --change-id ID --gate G --outcome approved|rejected
             --subject-revision R [--comment T] [--expected-state-revision N]

The API base URL defaults to ``http://127.0.0.1:8000`` (local port-forward of
``svc/dark-factory``, T-093 launchd agents); override with ``PILOT_API_BASE``.

This script is operator tooling: ``approve`` must be run by a human (ADR-011,
agents never approve or merge).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from typing import Any

DEFAULT_API_BASE = "http://127.0.0.1:8000"


def _api_base() -> str:
    return os.environ.get("PILOT_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _tokens() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (changes-write token, approvals operator token) from the environment.

    The local .env mirror of ``factory-api-tokens`` may hold unquoted keys, so
    the value is normalized to strict JSON before parsing. Neither form nor any
    token value is ever printed.
    """
    raw = os.environ.get("DARK_FACTORY_API_TOKENS", "")
    if not raw:
        raise SystemExit("DARK_FACTORY_API_TOKENS is not set; source the repo .env first")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        # The local .env mirror may hold a shorthand literal: bare identifier
        # keys, unquoted scalar values, unquoted scope lists. Normalize in
        # three steps - array elements first, then object keys, then bare
        # scalar values - so the key pass cannot mangle array entries.
        def _quote_array(match: "re.Match[str]") -> str:
            elements = [part.strip() for part in match.group(1).split(",")]
            return "[" + ", ".join(json.dumps(part) for part in elements if part) + "]"

        normalized = re.sub(r"\[([^\[\]]*)\]", _quote_array, raw)
        normalized = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', normalized)
        normalized = re.sub(
            r"(:\s*)([A-Za-z][A-Za-z0-9_\-]*)(\s*[,}\]])",
            lambda match: f"{match.group(1)}{json.dumps(match.group(2))}{match.group(3)}",
            normalized,
        )
        try:
            entries = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise SystemExit("DARK_FACTORY_API_TOKENS is not parseable as JSON") from exc
    if not isinstance(entries, list):
        raise SystemExit("DARK_FACTORY_API_TOKENS is not a JSON array")
    changes = approvals = None
    for entry in entries:
        scopes = set(entry.get("scopes", []))
        if "changes:write" in scopes and changes is None:
            changes = entry
        if entry.get("role") == "operator" and "approvals:write" in scopes and approvals is None:
            approvals = entry
    if changes is None:
        raise SystemExit("no token with the changes:write scope in DARK_FACTORY_API_TOKENS")
    return changes, approvals or {}


def _request(
    method: str, path: str, *, token: str | None = None, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    url = f"{_api_base()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = str(body.get("idempotency_key") or body.get("id") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload: Any = json.loads(response.read().decode("utf-8") or "null")
            return response.status, payload
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - error bodies are diagnostics only
            payload = None
        return exc.code, payload


def cmd_intake(args: argparse.Namespace) -> int:
    changes_token, _ = _tokens()
    with open(args.tasks_file, encoding="utf-8") as handle:
        matrix = json.load(handle)
    product = matrix["product"]
    created = replayed = failed = 0
    for task in matrix["tasks"]:
        status, payload = _request(
            "POST",
            "/api/v1/changes",
            token=changes_token["token"],
            body=change_body(task, product),
        )
        if status in (200, 201):
            outcome = "created" if status == 201 else "replayed"
            if status == 201:
                created += 1
            else:
                replayed += 1
            print(f"{task['id']}: {outcome}")
        else:
            failed += 1
            title = payload.get("title", "?") if isinstance(payload, dict) else "?"
            print(f"{task['id']}: HTTP {status} ({title})")
    print(f"intake: {created} created, {replayed} replayed, {failed} failed")
    return 1 if failed else 0


def change_body(task: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    """Build the Change intake document of one task (api.md §3)."""
    return {
        "id": task["id"],
        "title": task["title"],
        "description": task["description"],
        "source": task.get("source", "api"),
        "external_ref": task.get("external_ref"),
        "product": product,
        "risk_class": task["risk_class"],
    }


def _compact_change(change: dict[str, Any]) -> str:
    product = change.get("product", {})
    return (
        f"{change.get('id')}  {change.get('risk_class')}  {change.get('title')}"
        f"  [{product.get('provider')}:{product.get('slug')}]"
    )


def _compact_run(run: dict[str, Any]) -> str:
    return (
        f"run {run.get('id')}  {run.get('status')}  route={run.get('route')}"
        f"  state_revision={run.get('state_revision')}"
    )


def cmd_status(args: argparse.Namespace) -> int:
    if args.change_id:
        status, card = _request("GET", f"/api/v1/changes/{args.change_id}")
        if status != 200:
            print(f"change {args.change_id}: HTTP {status}")
            return 1
        assert isinstance(card, dict)
        print(_compact_change(card.get("change", card)))
        for run in card.get("runs", []):
            print(f"  {_compact_run(run)}")
            for stage in run.get("stages", []):
                print(
                    f"    {stage.get('stage')}: {stage.get('status')}"
                    f" (attempt={stage.get('attempt_number')})"
                )
        return 0
    status, payload = _request("GET", "/api/v1/changes?limit=200")
    if status != 200:
        print(f"changes list: HTTP {status}")
        return 1
    assert isinstance(payload, list)
    for change in payload:
        print(_compact_change(change))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    _, approvals = _tokens()
    if not approvals:
        raise SystemExit("no operator token with the approvals:write scope")
    body: dict[str, Any] = {
        "gate": args.gate,
        "outcome": args.outcome,
        "subject_revision": args.subject_revision,
    }
    if args.comment:
        body["comment"] = args.comment
    if args.expected_state_revision is not None:
        body["expected_state_revision"] = args.expected_state_revision
    status, payload = _request(
        "POST",
        f"/api/v1/changes/{args.change_id}/approvals",
        token=approvals["token"],
        body=body,
    )
    if status in (200, 201):
        assert isinstance(payload, dict)
        print(f"decision {payload.get('id')}: {payload.get('outcome')} (gate {args.gate})")
        return 0
    detail = payload.get("detail") if isinstance(payload, dict) else None
    print(f"approve failed: HTTP {status} {detail or ''}".rstrip())
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Factory API helper for the T-043 pilot")
    sub = parser.add_subparsers(dest="command", required=True)

    intake = sub.add_parser("intake", help="POST the task matrix to /api/v1/changes")
    intake.add_argument("tasks_file")
    intake.set_defaults(func=cmd_intake)

    status = sub.add_parser("status", help="list changes or show one change with its runs")
    status.add_argument("change_id", nargs="?")
    status.set_defaults(func=cmd_status)

    approve = sub.add_parser("approve", help="file an operator decision (human action)")
    approve.add_argument("--change-id", required=True)
    approve.add_argument("--gate", required=True)
    approve.add_argument("--outcome", required=True, choices=["approved", "rejected", "waived"])
    approve.add_argument("--subject-revision", required=True)
    approve.add_argument("--comment")
    approve.add_argument("--expected-state-revision", type=int)
    approve.set_defaults(func=cmd_approve)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
