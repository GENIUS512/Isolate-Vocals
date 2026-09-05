#!/bin/bash
# Устанавливаем жёсткие лимиты
ulimit -v 400000  # 400 МБ виртуальной памяти
ulimit -t 300     # 5 минут CPU времени

# Запускаем с одним воркером
exec uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --limit-concurrency 1 --timeout-keep-alive 30
