FROM python:3.10-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем приложение
COPY . .

# Создаем ссылку на CPU-версию PyTorch
ENV CUDA_VISIBLE_DEVICES=""
ENV PYTORCH_ENABLE_MPS_FALLBACK="1"

# Ограничиваем память
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1

EXPOSE 8000

# Запускаем с одним воркером
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
