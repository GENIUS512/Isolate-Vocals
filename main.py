"""
ОТДЕЛЬНО — единый сервис для Render.com (Docker, бесплатный план).
Один контейнер: FastAPI отдаёт статический фронтенд И обрабатывает
запросы на разделение вокала/инструментала через Demucs.

Подогнано под ограничение по памяти бесплатного тарифа Render (512 МБ):
- аудио обрабатывается кусками (--segment), а не целиком
- torch ограничен одним потоком, чтобы не раздувать память на аллокаторах
- уменьшен максимальный размер файла

Работает только с файлами, которые пользователь загружает сам.
Скачивание по внешним ссылкам (YouTube и т.п.) не реализовано.
"""

import os
import shutil
import subprocess
import uuid
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
MAX_FILE_SIZE_MB = 15   # держим маленьким — 512 МБ RAM на free-плане Render это диктуют
MAX_DURATION_SEC = 240  # ~4 минуты, подстраховка от долгих треков, которые не влезут в память

app = FastAPI(title="Отдельно — разделение вокала и инструментала")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_demucs(input_path: Path, out_dir: Path) -> Path:
    env = os.environ.copy()
    # Ограничиваем torch одним потоком — иначе многопоточные аллокации
    # ощутимо раздувают пиковую память на слабом CPU/RAM.
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"

    cmd = [
        "python", "-m", "demucs",
        "--two-stems", "vocals",
        "-n", "htdemucs",
        "-d", "cpu",
        "--segment", "7.5",   # Максимальный сегмент для htdemucs — 7.8 секунд
        "-o", str(out_dir),
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        # Выводим полную ошибку для отладки
        error_msg = result.stderr[-2000:] if result.stderr else "Нет вывода stderr"
        raise RuntimeError(f"Demucs завершился с ошибкой: {error_msg}")

    stem_dir = out_dir / "htdemucs" / input_path.stem
    if not stem_dir.exists():
        raise RuntimeError("Demucs не создал ожидаемую папку с результатами")
    return stem_dir


def convert_to_format(src_wav: Path, dst: Path, fmt: str):
    audio = AudioSegment.from_wav(src_wav)
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
    with open(input_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ — на бесплатном тарифе это предел по памяти")
            f.write(chunk)

    try:
        audio_check = AudioSegment.from_file(input_path)
        duration_sec = len(audio_check) / 1000.0
        if duration_sec > MAX_DURATION_SEC:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(413, f"Трек длиннее {MAX_DURATION_SEC // 60} минут — на бесплатном тарифе может не хватить памяти")
    except HTTPException:
        raise
    except Exception:
        pass  # если pydub не смог прочитать метаданные — просто пробуем разделить как есть

    try:
        stem_dir = run_demucs(input_path, job_dir / "out")
    except RuntimeError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, str(e))

    vocals_wav = stem_dir / "vocals.wav"
    instr_wav = stem_dir / "no_vocals.wav"

    # Проверяем, что файлы существуют
    if not vocals_wav.exists() or not instr_wav.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, "Demucs не создал ожидаемые файлы: vocals.wav или no_vocals.wav")

    vocals_out = job_dir / f"vocal.{output_format}"
    instr_out = job_dir / f"instrumental.{output_format}"

    if output_format == "wav":
        shutil.copy(vocals_wav, vocals_out)
        shutil.copy(instr_wav, instr_out)
    else:
        convert_to_format(vocals_wav, vocals_out, "mp3")
        convert_to_format(instr_wav, instr_out, "mp3")

    # чистим исходники и промежуточные файлы Demucs сразу — экономим диск
    shutil.rmtree(job_dir / "out", ignore_errors=True)
    input_path.unlink(missing_ok=True)

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
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
