#!/usr/bin/env bash
# PostToolUse hook: run the characterization tests after a file edit.
# Exit 2 sends stderr back to Droid so it can fix the failure.
cat > /dev/null   # hook input arrives as JSON on stdin; not needed here

cd "${FACTORY_PROJECT_DIR:-.}" || exit 0
[ -d tests ] || exit 0

if ! out=$(python3 -W ignore -m unittest discover -s tests 2>&1); then
  echo "Characterization tests failed after this edit:" >&2
  echo "$out" | tail -40 >&2
  exit 2
fi
exit 0
