## What this PR does

<!-- One sentence: which legacy screen/rule does this migrate or fix? -->

## Migration parity checklist (tick every line; N/A needs a reason)

Missions must NOT edit expected values in tests/test_characterization.py or
the SEED_* constants in target/tests/test_parity.py. New behavior gets new
tests, never modified expectations.

- [ ] Business rules match legacy `order_entry_screen`, including the quirks
      (discount truncated with `int()`, tax truncated to the cent)
- [ ] Money is integer cents end to end (no floats in stored/computed amounts)
- [ ] All SQL is parameterized (no string-built queries - legacy `search_screen`
      is the anti-pattern)
- [ ] Any check-then-write on shared data (stock, credit) is atomic
- [ ] Connections/cursors closed; no leaked handles
- [ ] Every rule touched has a test in `target/tests/test_parity.py` with
      expected values copied from the legacy oracle
- [ ] `./run_checks.sh` exits 0 locally

## Rule -> test map (add a row per rule this PR touches)

| Rule (AGENTS.md)             | Legacy test                        | Target test                  |
|------------------------------|------------------------------------|------------------------------|
| Gold 5% discount, truncated  | test_gold_discount_truncates       | _mission fills in_           |

## Testing done

<!-- Paste the tail of ./run_checks.sh output -->
