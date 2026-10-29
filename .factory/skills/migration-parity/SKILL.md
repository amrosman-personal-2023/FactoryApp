---
name: migration-parity
description: Check a change against the business rules in AGENTS.md and run the characterization tests. Use when porting legacy order-system code.
---

# Migration parity

1. Read the "Business rules that MUST be preserved" section of AGENTS.md.
2. Run `./run_checks.sh` (the single gate: legacy oracle + target smoke/parity).
3. For each rule in scope, list it as preserved, changed, or untested.
4. Every changed/untested rule needs a parity test in `target/tests/test_parity.py`
   with expected values copied from the legacy oracle, and a row in the PR
   template's rule -> test map.
5. Done means: `./run_checks.sh` exits 0 AND no rule is untested.
