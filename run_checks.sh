#!/usr/bin/env bash
# The single definition of "done" (AGENTS.md): a milestone is complete only
# when this script exits 0. Hooks, CI, and reviewer droids all run THIS file.
set -o pipefail
cd "$(dirname "$0")"

fail=0

echo "== Legacy characterization tests (parity oracle - do not weaken) =="
python3 -W ignore -m unittest discover -s tests -v || fail=1

echo "== Target web app: smoke + parity =="
if python3 -c "import fastapi" 2>/dev/null; then
  (cd target && python3 -m unittest discover -s tests -v) || fail=1
else
  echo "SKIP: fastapi not installed. Run: python3 -m pip install -r target/requirements.txt"
fi

exit $fail
