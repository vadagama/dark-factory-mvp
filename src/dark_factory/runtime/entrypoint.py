"""Process entry point: bind the assembled runtime to ``factory run advance`` (ADR-025).

``[project.scripts]`` points the ``factory`` console script here instead of at
``dark_factory.cli.main``: the CLI is core and may not import
``dark_factory.runtime`` (ADR-024 p.5), so the module that hands the assembled
adapters to the working path has to live in the composition layer itself. This
module is the whole of that binding.

Composition is *lazy per command*. Two commands need seams: ``factory run
advance`` runs through the durable driver (harness-backed stage executor,
SCM-derived revision resolver and the provider-facts observer of the wait
resolution, T-092 S3), and ``factory api serve`` exposes the CI stage
switchboard (T059, ADR-027). For those the process assembles a
:class:`~dark_factory.runtime.composition.Runtime` from its environment and
passes the bindings to the CLI. Every other command — ``doctor``, ``stage run``,
``run status``, ``reconcile``, the outbox commands, ``release verify`` — depends
on core alone, so no runtime is assembled and nothing from the environment is
read beyond what the command itself reads; their behaviour is exactly the CLI's.

``python -m dark_factory.cli`` deliberately stays a core path (deterministic
executor, no composition): it runs the same command tree without assembling a
process runtime — ``api serve`` then reports the CI toggles as unconfigured
rather than inventing a switchboard (fail-closed).

As with the rest of ``dark_factory.runtime``, this is binding and not logic
(ADR-024 p.5): the module recognizes the one command that needs the seams,
assembles the runtime from the process environment, hands the bindings over and
releases the assembled resources. It never decides what a stage does.
"""

import asyncio
from collections.abc import Sequence

from dark_factory.cli.main import ApiServeArgs, RunAdvanceArgs, parse_command
from dark_factory.cli.main import main as cli_main
from dark_factory.runtime.composition import build_runtime

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point (``factory``, pyproject ``[project.scripts]``).

    Returns the process exit code; the console-script wrapper raises
    ``SystemExit`` with it. Argument parsing is the CLI's, so an invalid command
    leaves with argparse's exit code 2 before anything is assembled.

    For ``run advance`` the runtime is assembled first and its bindings are passed
    to the CLI: the executor drives the stage, the revision resolver keys it by
    the product commit and the provider-facts observer resolves a waiting
    stage's external wait (T-092 S3). For ``api serve`` the same runtime supplies
    the CI stage switchboard (``ci_toggles``/``ci_repository``, T059) — absent
    credentials leave it ``None`` and the API reports the toggles unconfigured.
    The runtime is released in a ``finally``, so the adapters' resources (HTTP
    pool, tracer provider) are freed even when the command fails.
    """
    command = parse_command(argv)
    if isinstance(command, ApiServeArgs):
        runtime = build_runtime()
        try:
            return cli_main(
                argv,
                ci_toggles=runtime.ci_stage_toggles,
                ci_repository=runtime.ci_repository,
            )
        finally:
            asyncio.run(runtime.aclose())
    if not isinstance(command, RunAdvanceArgs):
        return cli_main(argv)
    runtime = build_runtime()
    try:
        return cli_main(
            argv,
            executor=runtime.agent_stage_executor(),
            revision_of=runtime.revision_of(),
            gate_facts=runtime.facts_provider(),
        )
    finally:
        asyncio.run(runtime.aclose())
