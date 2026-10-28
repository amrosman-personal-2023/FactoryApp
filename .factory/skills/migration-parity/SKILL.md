---
name: migration-parity
description: Check a change against the business rules in AGENTS.md and run the characterization tests. Use when porting legacy order-system code.
---

# Migration parity

1. Read the "Business rules that MUST be preserved" section of AGENTS.md.
2. Run the tests in tests/ and list any rule without a test.
3. Report each rule as preserved, changed, or untested.
