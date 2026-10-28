# Factory setup: prompts for the validation automations + harness files

Sources: docs.factory.com pages read 28 Oct 2026 (software-factory/code-review-ci, automated-qa, security-review, wiki/overview, harness/skills, harness/hooks, harness/subagents, agent-readiness/readiness-report). Untested on your account. Replace anything that differs from what you see.

Keep this file OUT of the public repo.

## Step 0: repo hygiene (do first, the repo is public)
- [ ] Remove from git: `.DS_Store`, `Legacy/orders.db`, `tests/__pycache__/`, `Documents/Factory-Second-Meeting.pptx` (speaker notes and customer framing). Add `.gitignore`. Or make the repo private.
- [ ] Fix the path case: the folder is `Legacy/`, but `tests/test_characterization.py` imports from `../legacy`. macOS ignores the case; GitHub Actions and Droid Computers (Linux) do not, so CI would fail. Rename the folder to `legacy/` or change the path in the test.
- [ ] Copy `AGENTS.md` into the repo root (it is not in the repo yet) and update its paths.
- [ ] Re-tag `start` after cleanup (it currently points at the old `V1.1` commit).
- [ ] In Factory: install the Factory Droid GitHub App (`app.factory.ai/settings/organization`) and create an API key (`app.factory.ai/settings/api-keys`); add it to the repo as the secret `FACTORY_API_KEY`.
- [ ] Run `/readiness-report` once now and save the result (your "before" for a before/after).
- [ ] Do not open a PR for the plain-port baseline branch, or these automations will review it and muddy the comparison.

## Prompt 1: Code Review + Security Review (one CI workflow)
Security review is switched on inside the same workflow, so this one prompt covers two of the four automations.

```
/install-code-review
```
Then paste:
```
Set up automated code review and security review on this GitHub repository.
- Use the Factory Droid GitHub App.
- review_depth: shallow (cheaper for the demo; I can switch to deep later).
- Set automatic_review: true and automatic_security_review: true.
- Trigger on pull requests: opened, synchronize, reopened, ready for review. Skip drafts and PRs titled [skip-review].
- Create .factory/skills/review-guidelines/SKILL.md with these team standards:
  money is stored as integer cents; business rules must match AGENTS.md; every behavior change needs a test; SQL must be parameterized; any check-then-write on shared data needs a transaction or a guard; connections and files must be closed; endpoints that change data need authentication.
- Create .factory/skills/security-review-guidelines/SKILL.md with: flag string-built SQL, missing authentication or authorization on state-changing routes, hardcoded secrets, and unvalidated input from forms.
- Open a PR with the workflow and skill files. Do not merge it. Tell me which secrets I need to add.
```
Trade-off: these guidelines are real team standards but they also point at the planted issues. The plain-port baseline will not have them. Say so on the scorecard rather than implying the tool found them unaided.

## Prompt 2: Automated QA
```
/install-qa
```
Then paste:
```
Set up automated QA for this repository.
- Apps: (1) the legacy terminal app in legacy/ordersys.py, driven through a virtual terminal; (2) the new web app in target/ (FastAPI + HTMX, started with uvicorn on port 8000), driven in a browser.
- Critical flows: create an order for a Platinum customer with quantity 100 and check the 13 percent discount and the total; reject an order that exceeds the credit limit; reject an order that exceeds stock; status changes New to Picked to Shipped and the cancel rules; customer name search with normal and hostile input.
- Seed the database before each run with db.py. No authentication is configured; do not invent credentials.
- Run on pull requests, post the report as a PR comment, and make the check optional.
- Failure learning: suggestions in the report only.
- List any secrets I must add.
```
Note: the docs say QA can drive CLI/TUI apps through a virtual terminal (`tuistory`). That could answer the readiness gap "no scriptable way to exercise the green screen". VERIFY it works with curses.

## Prompt 3: AutoWiki
```
/wiki
```
Then, once the first wiki looks right:
```
/install-wiki
```
Then paste:
```
Set up automatic wiki refresh for this GitHub repository: regenerate on every push to the default branch, with cloud sync to the Factory App. Do NOT push to the GitHub Wiki tab (the repo is public). Open a PR with the workflow and tell me which secrets I need.
```
Note: per the docs, refresh is on push to the default branch, not on pull requests. For the demo, the wiki should be generated before the migration and again after the merge, so you can show it picking up the change.

## Harness files (formats now confirmed from the docs)

Skill, `.factory/skills/migration-parity/SKILL.md`:
```
---
name: migration-parity
description: Check a change against the business rules in AGENTS.md and run the characterization tests. Use when porting legacy order-system code.
---

# Migration parity

1. Read the "Business rules that MUST be preserved" section of AGENTS.md.
2. Run the tests in tests/ and list any rule without a test.
3. Report each rule as preserved, changed, or untested.
```

Hook, `.factory/hooks.json` (runs after file edits; exit code 2 sends the failure back to Droid):
```
{
  "PostToolUse": [
    {
      "matcher": "Create|Edit|ApplyPatch",
      "hooks": [
        { "type": "command",
          "command": "bash \"$FACTORY_PROJECT_DIR\"/.factory/hooks/run_tests.sh",
          "timeout": 120 }
      ]
    }
  ]
}
```
`.factory/hooks/run_tests.sh` must run the tests and `exit 2` with the output on stderr when they fail.

Reviewer droid, `.factory/droids/migration-reviewer.md`:
```
---
name: migration-reviewer
description: Reviews a legacy-to-web migration for behavior parity, security and concurrency
model: inherit
tools: read-only
---

You review migrated code against AGENTS.md. For each change:
- compare every business rule with the legacy behavior, including rounding and truncation
- flag SQL built from strings, missing authentication, and check-then-write without a transaction
- list tests that are missing before merge
```

## Recommended order
1. Step 0 hygiene, then `/readiness-report` (before).
2. Prompt 1, 2, 3: merge each setup PR.
3. Skill, hook, droid; commit; re-run `/readiness-report` (after).
4. Tag `start`, then begin the plain-port and Factory runs.
