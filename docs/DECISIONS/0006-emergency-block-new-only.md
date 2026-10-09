# ADR 0006 — Interim emergency: block new orders only

**Status:** Proposed  
**Date:** 2026-10-09  
**Related:** [../RISK_POLICY.md](../RISK_POLICY.md), [../OPERATIONS.md](../OPERATIONS.md)

## Context

The PRD requires separate definitions for blocking new orders, canceling open orders, and protecting/closing positions. Automatic cancel/close can reduce exposure but can also realize losses or conflict with owner intent. No owner-approved emergency exit policy exists yet.

## Decision

Until the owner approves `RP-EMERGENCY-CANCEL` and/or `RP-EMERGENCY-PROTECT`:

* On critical failure, stale critical state, or unknown order risk: **block new orders** and alert the owner.
* Do **not** automatically cancel open orders or close/reduce positions.

## Consequences

* Safer against unintended liquidation behavior during early phases.
* Leaves open exposure until the owner acts.
* Must be revisited before live activation if automated protect is desired.
