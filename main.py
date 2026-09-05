"""
ОТДЕЛЬНО — единый сервис для Render.com (Docker, бесплатный план).
Оптимизированная версия с минимальным потреблением памяти.
"""

import os
import shutil
import subprocess
import uuid
import gc
from pathlib import Path

# ВАЖНО: импортируем numpy и настраиваем torch до всего остального
import numpy as np
import torch

# Настройка PyTorch для минимального потребления памяти
torch.set_num_threads(1)
torch.set_default_dtype(torch.float32)

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydub import AudioSegment

APP_ROOT = Path(__file__).parent
FRONTEND_DIR = APP_ROOT / "frontend"
JOBS_DIR = APP_ROOT / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
MAX_FILE_SIZE_MB = 10   # Уменьшаем до 10 МБ для экономии памяти
MAX_DURATION_SEC = 180  # Уменьшаем до 3 минут

app = FastAPI(title="Отдельно — разделение вокала и инструментала")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_demucs(input_path: Path, out_dir: Path) -> Path:
    """Запускает Demucs с минимальным потреблением памяти"""
    env = os.environ.copy()
    
    # Жёсткие ограничения для экономии памяти
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:32"
    env["PYTHONHASHSEED"] = "0"
    
    # Используем более лёгкую модель и меньший сегмент
    cmd = [
        "python", "-m", "demucs",
        "--two-stems", "vocals",
        "-n", "hdemucs",  # Используем более лёгкую модель вместо htdemucs
        "-d", "cpu",
        "--segment", "4",  # Ещё меньше сегмент для экономии памяти
        "-o", str(out_dir),
        "--shifts", "1",   # Меньше сдвигов для ускорения
        "--overlap", "0.25",  # Меньше перекрытия
        str(input_path),
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Demucs превысил время выполнения (10 минут)")
    
    if result.returncode != 0:
        error_msg = result.stderr[-2000:] if result.stderr else "Нет вывода stderr"
        raise RuntimeError(f"Demucs завершился с ошибкой: {error_msg}")

    # Пытаемся найти папку с результатами (может быть hdemucs или htdemucs)
    out_path = Path(out_dir)
    possible_dirs = ["hdemucs", "htdemucs", "mdx_extra_q"]
    
    for model_dir in possible_dirs:
        stem_dir = out_path / model_dir / input_path.stem
        if stem_dir.exists():
            return stem_dir
    
    # Если не нашли, пробуем найти любую папку с результатами
    for subdir in out_path.iterdir():
        if subdir.is_dir():
            stem_dir = subdir / input_path.stem
            if stem_dir.exists():
                return stem_dir
    
    raise RuntimeError("Demucs не создал ожидаемую папку с результатами")


def convert_to_format(src_wav: Path, dst: Path, fmt: str):
    """Конвертирует WAV в указанный формат"""
    try:
        audio = AudioSegment.from_wav(src_wav)
        # Уменьшаем качество для экономии памяти
        if fmt == "mp3":
            audio.export(dst, format=fmt, bitrate="128k")
        else:
            audio.export(dst, format=fmt)
    except Exception as e:
        raise RuntimeError(f"Ошибка конвертации: {str(e)}")


@app.post("/api/separate")
async def separate(file: UploadFile = File(...), output_format: str = "mp3"):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Формат {ext} не поддерживается")
    if output_format not in {"mp3", "wav"}:
        raise HTTPException(400, "output_format должен быть mp3 или wav")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True)

    input_path = job_dir / f"input{ext}"
    size = 0
    
    # Сохраняем файл с ограничением по размеру
    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 512):  # Читаем меньшими кусками
                size += len(chunk)
                if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"Ошибка загрузки файла: {str(e)}")

    # Проверяем длительность
    try:
        audio_check = AudioSegment.from_file(input_path)
        duration_sec = len(audio_check) / 1000.0
        if duration_sec > MAX_DURATION_SEC:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(413, f"Трек длиннее {MAX_DURATION_SEC // 60} минут")
        # Освобождаем память
        del audio_check
        gc.collect()
    except HTTPException:
        raise
    except Exception:
        pass

    # Запускаем Demucs
    try:
        stem_dir = run_demucs(input_path, job_dir / "out")
    except RuntimeError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, str(e))

    # Проверяем результаты
    vocals_wav = stem_dir / "vocals.wav"
    instr_wav = stem_dir / "no_vocals.wav"

    if not vocals_wav.exists() or not instr_wav.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, "Demucs не создал ожидаемые файлы")

    # Конвертируем результаты
    try:
        vocals_out = job_dir / f"vocal.{output_format}"
        instr_out = job_dir / f"instrumental.{output_format}"

        if output_format == "wav":
            shutil.copy(vocals_wav, vocals_out)
            shutil.copy(instr_wav, instr_out)
        else:
            convert_to_format(vocals_wav, vocals_out, "mp3")
            convert_to_format(instr_wav, instr_out, "mp3")
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"Ошибка конвертации: {str(e)}")

    # Чистим временные файлы
    shutil.rmtree(job_dir / "out", ignore_errors=True)
    input_path.unlink(missing_ok=True)
    gc.collect()

    return {
        "job_id": job_id,
        "vocal_url": f"/api/download/{job_id}/vocal.{output_format}",
        "instrumental_url": f"/api/download/{job_id}/instrumental.{output_format}",
    }


@app.get("/api/download/{job_id}/{filename}")
async def download(job_id: str, filename: str):
    path = JOBS_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "Файл не найден или уже удалён")
    return FileResponse(path, filename=filename)


@app.delete("/api/jobs/{job_id}")
async def cleanup(job_id: str):
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    gc.collect()
    return {"ok": True}


@app.get("/api/health")
async def health():
    # Проверяем, что приложение живо
    return {"status": "ok"}


# Монтируем статику в самом конце
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Обработчик ошибок для 502
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback
    print(f"Global error: {exc}")
    print(traceback.format_exc())
    return HTTPException(500, f"Внутренняя ошибка сервера: {str(exc)}")
