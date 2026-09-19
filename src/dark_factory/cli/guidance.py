"""Render ``Guidance`` for the terminal (T073/T074, ADR-033 p.3).

The CLI does not decide the next step: it prints the block the core computed —
the same object the Console renders — after ``factory change create`` and in
``factory change status``. One layout, so an operator recognises it everywhere:

.. code-block:: text

    Следующий шаг: <headline>
      почему: <why>
      → <primary label>  (cli: …)  (api: …)
      также: <label> (cli: …) · <label> (api: …)
      блокеры:
        - <what> — снимает: <who> — как: <how>
      после: <after>
"""

from dark_factory.orchestration.guidance import Guidance, GuidanceAction

__all__ = ["render_guidance_text"]

_ACTOR_LABEL = {
    "operator": "оператор",
    "agent": "агент",
    "ci": "CI",
    "factory": "фабрика",
    "external": "внешняя система",
}


def _action(action: GuidanceAction) -> str:
    parts = [action.label]
    if action.cli:
        parts.append(f"(cli: {action.cli})")
    if action.api:
        parts.append(f"(api: {action.api})")
    if not action.enabled:
        parts.append(f"— недоступно: {action.reason or 'причина не указана'}")
    return " ".join(parts)


def render_guidance_text(guidance: Guidance) -> str:
    """The next-step block, one line per fact; empty parts are omitted."""
    lines = [f"Следующий шаг: {guidance.headline}", f"  почему: {guidance.why}"]
    lines.append(f"  → {_action(guidance.primary)}")
    if guidance.secondary:
        lines.append("  также: " + " · ".join(_action(action) for action in guidance.secondary))
    if guidance.blockers:
        lines.append("  блокеры:")
        for blocker in guidance.blockers:
            who = _ACTOR_LABEL[blocker.who.value]
            lines.append(f"    - {blocker.what} — снимает: {who} — как: {blocker.how}")
    if guidance.after:
        lines.append(f"  после: {guidance.after}")
    return "\n".join(lines)
