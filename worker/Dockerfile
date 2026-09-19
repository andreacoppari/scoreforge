FROM python:3.11-slim-bookworm AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    musescore3 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .

FROM base AS cpu
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY app ./app
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data /home/appuser/.cache && chown -R appuser:appuser /app /data /home/appuser/.cache
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
