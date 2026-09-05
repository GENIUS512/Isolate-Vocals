#!/bin/bash
# Устанавливаем лимиты
ulimit -v 450000  # 450 МБ виртуальной памяти
ulimit -t 600     # 10 минут CPU времени

# Запускаем без ограничения конкурентности
exec uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
