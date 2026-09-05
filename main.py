"""
ОТДЕЛЬНО — единый сервис для Render.com с оптимизацией памяти
"""

import os
import shutil
import subprocess
import uuid
import gc
from pathlib import Path

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
MAX_FILE_SIZE_MB = 8   # 8 МБ для экономии памяти
MAX_DURATION_SEC = 120  # 2 минуты

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
    
    # Максимальные ограничения для экономии памяти
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["PYTHONHASHSEED"] = "0"
    
    # Используем самую лёгкую модель
    cmd = [
        "python", "-m", "demucs",
        "--two-stems", "vocals",
        "-n", "htdemucs",
        "-d", "cpu",
        "--segment", "4",
        "-o", str(out_dir),
        "--shifts", "1",
        "--overlap", "0.25",
        str(input_path),
    ]
    
    try:
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            env=env,
            timeout=300  # 5 минут максимум
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Demucs превысил время выполнения")
    
    if result.returncode != 0:
        error_msg = result.stderr[-2000:] if result.stderr else "Нет вывода stderr"
        raise RuntimeError(f"Demucs завершился с ошибкой: {error_msg}")

    # Поиск результатов
    out_path = Path(out_dir)
    possible_dirs = ["htdemucs", "hdemucs", "mdx_extra_q"]
    
    for model_dir in possible_dirs:
        stem_dir = out_path / model_dir / input_path.stem
        if stem_dir.exists():
            return stem_dir
    
    # Если не нашли, ищем любую папку
    for subdir in out_path.iterdir():
        if subdir.is_dir():
            stem_dir = subdir / input_path.stem
            if stem_dir.exists():
                return stem_dir
    
    raise RuntimeError("Папка с результатами не найдена")


def convert_to_format(src_wav: Path, dst: Path, fmt: str):
    """Конвертирует WAV в указанный формат"""
    audio = AudioSegment.from_wav(src_wav)
    if fmt == "mp3":
        audio.export(dst, format=fmt, bitrate="96k")
    else:
        audio.export(dst, format=fmt)


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
    
    # Сохраняем файл
    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 256):
                size += len(chunk)
                if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"Ошибка загрузки: {str(e)}")

    # Проверяем длительность
    try:
        audio_check = AudioSegment.from_file(input_path)
        duration_sec = len(audio_check) / 1000.0
        if duration_sec > MAX_DURATION_SEC:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(413, f"Трек длиннее {MAX_DURATION_SEC // 60} минут")
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
        raise HTTPException(500, "Файлы результатов не найдены")

    # Конвертируем
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

    # Чистим
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
        raise HTTPException(404, "Файл не найден")
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
    # Простой ответ для проверки здоровья
    return {"status": "ok", "memory": "healthy"}


# Главная страница
@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Монтируем статику после всех эндпоинтов
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
