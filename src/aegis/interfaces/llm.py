"""LLM supervisor port — schema-constrained completions.

Token / cost / latency budgets are enforced by ``aegis.supervisor``, not by LlmPort.
Owner-selected OpenAI-compatible providers: ``gemini``, ``openrouter``, ``groq``
(see ``aegis.supervisor.factory.build_llm_port``). Empty config → UnavailableLlmPort.
"""

from __future__ import annotations

from typing import Any, Protocol

from aegis.schemas.proposal import ProposalAction


class LlmPort(Protocol):
    def complete_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Return a structured object matching the requested schema.

        Optional usage metadata may be returned under ``_meta`` (e.g. token counts);
        the supervisor strips ``_meta`` before schema validation and enforces budgets.
        """


class UnavailableLlmPort:
    """Fail closed: no fabricated proposals."""

    def complete_structured(
        self,
        *,
        prompt: str,
        schema_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        _ = prompt, timeout_seconds
        raise RuntimeError(
            f"LLM provider is not configured. Refusing structured completion for {schema_name!r}."
        )

    def no_trade_stub(self) -> ProposalAction:
        """Helper documenting the preferred fail-closed action."""
        return ProposalAction.NO_TRADE
