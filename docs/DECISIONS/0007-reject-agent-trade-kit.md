# ADR 0007 — Reject Toobit Agent Trade Kit for Aegis

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../THREAT_MODEL.md](../THREAT_MODEL.md), [../RFC.md](../RFC.md)

## Context

Toobit publishes an Agent Trade Kit / MCP server that can expose order placement tools to AI clients. Aegis forbids LLMs and Jev from submitting orders or receiving exchange credentials.

## Decision

Do not deploy or depend on the Toobit Agent Trade Kit (or similar AI-facing order tool bridges) as part of Aegis. Exchange access is only through the Aegis Toobit adapter after Risk Engine approval.

## Consequences

* Clear separation from Toobit’s AI agent product surface.
* Operators must not share Aegis exchange keys with MCP order tools.
* Market-data-only use of third-party tools remains unnecessary for Aegis core; prefer first-party adapter.
