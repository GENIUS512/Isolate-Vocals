#!/bin/bash
# Устанавливаем лимиты
ulimit -v 450000  # 450 МБ виртуальной памяти
ulimit -t 600     # 10 минут CPU времени

# Экспортируем переменные окружения
export CUDA_VISIBLE_DEVICES=""
export PYTORCH_ENABLE_MPS_FALLBACK="1"
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

# Запускаем
exec uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
