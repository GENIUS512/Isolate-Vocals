#!/bin/bash
# Устанавливаем лимиты для предотвращения падений
ulimit -v 450000  # Ограничение виртуальной памяти ~450 МБ
ulimit -t 600     # Ограничение CPU времени 10 минут

# Запускаем приложение
uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --limit-concurrency 1
