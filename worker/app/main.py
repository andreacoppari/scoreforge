import asyncio
import importlib.util
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import get_settings
from .models import Instrument, JobResponse, JobStatus
from .pipeline import separate, transcribe

settings = get_settings()
settings.jobs_dir.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="ScoreForge worker", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

jobs: dict[str, JobResponse] = {}
job_dirs: dict[str, Path] = {}
semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
SCORE_EXTENSIONS = {".mid", ".midi", ".musicxml", ".xml"}


def _safe_extension(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(415, "Use MP3, WAV, M4A, FLAC, OGG, or AAC audio.")
    return suffix


async def _save_upload(upload: UploadFile, job_dir: Path) -> Path:
    suffix = _safe_extension(upload.filename)
    target = job_dir / f"input{suffix}"
    total = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"Upload limit is {settings.max_upload_mb} MB.")
            handle.write(chunk)
    if not total:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded audio file is empty.")
    return target


async def _run(
    job_id: str,
    audio_path: Path,
    instruments: list[Instrument],
    engine: str,
    transcription_mode: str = "melody",
) -> None:
    job = jobs[job_id]
    async with semaphore:
        job.status = JobStatus.running
        job.progress = 10
        job.message = "Loading local model…"
        try:
            output_dir = job_dirs[job_id] / "output"
            if job.kind == "transcribe":
                job.progress = 35
                job.message = "Detecting notes…"
                artifacts = await asyncio.to_thread(
                    transcribe,
                    audio_path,
                    output_dir,
                    instruments,
                    engine,
                    settings.muscriptor_model,
                    settings.device,
                    transcription_mode,
                )
            else:
                job.progress = 30
                job.message = "Separating vocals from music…"
                artifacts = await asyncio.to_thread(
                    separate, audio_path, output_dir, settings.demucs_model, settings.device
                )
            job.artifacts = artifacts
            job.status = JobStatus.succeeded
            job.progress = 100
            job.message = "Ready to download."
        except Exception as exc:  # Models surface varied low-level exceptions.
            job.status = JobStatus.failed
            job.progress = 100
            job.message = "Processing failed."
            job.error = str(exc)[:1000]


async def _cleanup_expired() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=settings.job_ttl_hours)
    for job_id, job_dir in list(job_dirs.items()):
        if datetime.fromtimestamp(job_dir.stat().st_mtime, UTC) < cutoff:
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dirs.pop(job_id, None)
            jobs.pop(job_id, None)


def _musescore_binary() -> str | None:
    return shutil.which("mscore") or shutil.which("musescore3")


def _export_printable_pdf(job_id: str, source_name: str) -> Path:
    """Engrave a completed score locally and reuse its cached PDF thereafter."""
    if job_id not in job_dirs or not re.fullmatch(r"[A-Za-z0-9_.-]+", source_name):
        raise HTTPException(404, "Score not found.")

    source = job_dirs[job_id] / "output" / source_name
    if source.suffix.lower() not in SCORE_EXTENSIONS or not source.is_file():
        raise HTTPException(404, "Score not found.")

    pdf_path = source.with_suffix(".pdf")
    if pdf_path.is_file() and pdf_path.stat().st_size > 0:
        return pdf_path

    binary = _musescore_binary()
    if not binary:
        raise HTTPException(503, "PDF rendering is unavailable. Rebuild the worker with MuseScore.")

    config_dir = job_dirs[job_id] / ".musescore"
    config_dir.mkdir(exist_ok=True)
    environment = os.environ | {
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_CONFIG_HOME": str(config_dir),
    }
    try:
        result = subprocess.run(
            [binary, str(source), "-o", str(pdf_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "PDF rendering timed out.") from exc

    if result.returncode != 0 or not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        pdf_path.unlink(missing_ok=True)
        detail = (result.stdout or "MuseScore could not engrave this score.").strip()
        raise HTTPException(500, f"PDF rendering failed: {detail[-600:]}")
    return pdf_path


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "engine": settings.transcription_engine,
        "device": settings.device,
        "capabilities": {
            "basic_pitch": True,
            "muscriptor": importlib.util.find_spec("muscriptor") is not None,
            "voice_music_separation": True,
            "pdf_export": _musescore_binary() is not None,
        },
    }


@app.post("/api/jobs/transcribe", response_model=JobResponse, status_code=202)
async def create_transcription(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    instruments: list[Instrument] = Form(default=[]),
    engine: str | None = Form(default=None),
    transcription_mode: str = Form(default="melody"),
) -> JobResponse:
    selected_engine = (engine or settings.transcription_engine).lower()
    if selected_engine not in {"basic-pitch", "muscriptor"}:
        raise HTTPException(400, "Engine must be basic-pitch or muscriptor.")
    # Accept names used by an older browser build while keeping the public
    # vocabulary aligned with the two distinct score meanings.
    transcription_mode = {"arrange": "melody", "detect": "single_part"}.get(
        transcription_mode, transcription_mode
    )
    if transcription_mode not in {"melody", "single_part"}:
        raise HTTPException(400, "Transcription mode must be melody or single_part.")
    if transcription_mode == "single_part" and len(instruments) != 1:
        raise HTTPException(400, "Single part mode requires exactly one instrument.")
    job_id = uuid.uuid4().hex
    job_dir = settings.jobs_dir / job_id
    job_dir.mkdir(parents=True)
    audio_path = await _save_upload(file, job_dir)
    job = JobResponse(
        id=job_id,
        kind="transcribe",
        status=JobStatus.queued,
        progress=0,
        message="Queued for local processing…",
        engine=selected_engine,
    )
    jobs[job_id] = job
    job_dirs[job_id] = job_dir
    background_tasks.add_task(
        _run, job_id, audio_path, instruments, selected_engine, transcription_mode
    )
    background_tasks.add_task(_cleanup_expired)
    return job


@app.post("/api/jobs/separate", response_model=JobResponse, status_code=202)
async def create_separation(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> JobResponse:
    job_id = uuid.uuid4().hex
    job_dir = settings.jobs_dir / job_id
    job_dir.mkdir(parents=True)
    audio_path = await _save_upload(file, job_dir)
    job = JobResponse(
        id=job_id,
        kind="separate",
        status=JobStatus.queued,
        progress=0,
        message="Queued for local processing…",
    )
    jobs[job_id] = job
    job_dirs[job_id] = job_dir
    background_tasks.add_task(_run, job_id, audio_path, [], "")
    background_tasks.add_task(_cleanup_expired)
    return job


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    if job_id not in jobs:
        raise HTTPException(404, "Job not found or expired.")
    return jobs[job_id]


@app.get("/api/jobs/{job_id}/downloads/{name}")
async def download(job_id: str, name: str, request: Request) -> FileResponse:
    if job_id not in job_dirs or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise HTTPException(404, "Artifact not found.")
    path = job_dirs[job_id] / "output" / name
    if not path.is_file():
        raise HTTPException(404, "Artifact not found.")
    return FileResponse(path, filename=name, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/jobs/{job_id}/print/{source_name}")
async def printable_pdf(job_id: str, source_name: str) -> FileResponse:
    pdf_path = await asyncio.to_thread(_export_printable_pdf, job_id, source_name)
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{pdf_path.name}"',
        },
    )
