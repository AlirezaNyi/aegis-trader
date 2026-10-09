# ADR 0002 — Explicit orchestration (no LangGraph tool loop)

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../RFC.md](../RFC.md), [../PRD.md](../PRD.md)

## Context

The PRD lists LangGraph or explicit orchestration. Safety rules forbid models from executing orders or holding exchange credentials. A graph framework with tools increases the chance of accidental tool wiring.

## Decision

Use an explicit in-process orchestrator that calls analysts → evidence → Jev → Supervisor → Risk → Order Manager in code. Defer LangGraph (and similar agent tool loops) unless a future ADR proves need without granting execution tools to models.

## Consequences

* Clearer audit of control flow.
* Slightly more hand-written glue code.
* Supervisor remains schema-in / schema-out without tools.
