# ADR 0003 — PostgreSQL system of record; Redis deferred

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../RFC.md](../RFC.md), [../DATA_MODEL.md](../DATA_MODEL.md)

## Context

The PRD allows Redis only where justified. Initial workloads (candles, proposals, orders, audit) fit relational storage. Extra moving parts hurt a single small host.

## Decision

Use PostgreSQL as the system of record. Defer Redis (or other brokers/caches) until a measured need appears (e.g., multi-process fan-out) and a new ADR accepts the complexity.

## Consequences

* One primary durable store.
* In-process queues/schedulers for early phases.
* May revisit if latency or fan-out demands a cache/bus.
