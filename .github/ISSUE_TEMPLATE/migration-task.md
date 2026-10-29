---
name: Migration task
about: Track one slice of the ORDERSYS legacy-to-web migration
title: "Migrate <screen/rule> to the web app"
labels: enhancement
---

## Scope

<!-- Which legacy screen(s)/rule(s)? e.g. order_entry_screen, sales report -->

## Legacy behavior to preserve (quote AGENTS.md; quirks included)

- e.g. Discount truncated with int(), not rounded
- e.g. Status flow N -> P -> S; cancel only from N or P

## Acceptance criteria (the mission is done when ALL are true)

- [ ] Endpoint(s)/page(s) implemented in `target/` following the
      customers reference pattern (rules in services/, routers thin)
- [ ] Parity tests added in `target/tests/test_parity.py`, expected values
      copied from `tests/test_characterization.py`
- [ ] Smoke test extended in `target/tests/test_smoke.py`
- [ ] `./run_checks.sh` exits 0
- [ ] migration-reviewer droid findings addressed
