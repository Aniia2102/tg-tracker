#!/usr/bin/env bash
# Симуляции без сети: Telegram и Gmail подменены заглушками.
# Запуск из корня репозитория: bash tests/run.sh
cd "$(dirname "$0")/.." || exit 1
fail=0
for f in tests/sim_*.py; do
  if python3 "$f" >/tmp/tg_tracker_test.log 2>&1; then
    echo "ok    $f"
  else
    echo "FAIL  $f"; tail -20 /tmp/tg_tracker_test.log; fail=1
  fi
done
exit $fail
