# ADR 0001 — Modular monolith

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../RFC.md](../RFC.md), [../ARCHITECTURE.md](../ARCHITECTURE.md)

## Context

Aegis needs specialized analysts and a strict execution boundary, but the initial host is modest (~4 vCPU, 8 GB RAM). The PRD allows a modular monolith and rejects premature microservices.

## Decision

Implement Aegis as a single deployable Python package `aegis` with clear internal modules. Do not create one microservice per agent unless a later ADR justifies isolation, reliability, or scaling needs.

## Consequences

* Simpler ops and shared transactions.
* Stronger need for in-process isolation of credentials and careful concurrency limits.
* Future split remains possible at module boundaries.
