# Aegis — Final System Audit

Perform an independent end-to-end audit of Aegis against its approved PRD, SRS, RFC, architecture, risk policy, and test plan.

Verify:

1. No model or agent can bypass the deterministic Risk Engine.
2. Live trading is disabled by default and cannot be activated implicitly.
3. Missing or stale critical data blocks new orders.
4. Ambiguous order-submission outcomes trigger reconciliation, not blind retries.
5. Duplicate order submission is prevented.
6. Exchange credentials are inaccessible to analytical agents and LLMs.
7. Withdrawal permissions are not requested.
8. Risk policies are versioned, deterministic, tested, and auditable.
9. Orders, fills, positions, and balances reconcile against the exchange.
10. New-order blocking is distinct from open-order cancellation and open-position protection.
11. Backtesting avoids look-ahead bias and accounts for modeled transaction costs.
12. Paper account state is separated from live account state.
13. Toobit and Jev capabilities are supported by verified documentation or clearly marked unresolved assumptions.
14. Resource use, retries, concurrency, and model/API costs are bounded.
15. Monitoring, incident response, backups, and recovery are documented and tested.

Run the available tests and targeted safety tests. Do not place real trades.

Deliver:

* Executive summary.
* Findings by severity with evidence.
* Test results.
* Security and operational readiness.
* Backtesting and paper-trading limitations.
* Unresolved assumptions and risks.
* Recommendation: NOT READY, PAPER-TRADING READY, or READY FOR OWNER REVIEW OF LIVE ACTIVATION.

Passing tests does not establish profitability or guarantee safety. Do not enable live trading. Live activation requires a separate explicit decision by the project owner.
