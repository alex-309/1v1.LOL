#!/bin/bash
# Every test drives Game directly on a virtual clock -- no sockets, no sleeping.
# Run from anywhere:  tests/run.sh
cd "$(dirname "$0")/.." || exit 1
python3 -m py_compile server.py app.py || { echo "PYTHON SYNTAX FAIL"; exit 1; }
echo "───── js syntax"
python3 tests/jscheck.py || exit 1
echo
fail=0
for t in tests/t_*.py; do
  echo "───── $(basename "$t")"
  python3 "$t" || fail=1
  echo
done
[ $fail -eq 0 ] && echo "ALL SUITES PASSED" || echo "SUITE FAILURES"
exit $fail
