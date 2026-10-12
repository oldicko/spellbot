FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    VOICE_PATH=/app/voices/en_GB-cori-high.onnx \
    TZ=Europe/London \
    PORT=8080

WORKDIR /app

# onnxruntime needs libgomp at runtime on the slim image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cori high is the same British speaker as medium, with a cleaner recording.
RUN mkdir -p /app/voices \
    && python -m piper.download_voices en_GB-cori-high --data-dir /app/voices

COPY app ./app
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "-m", "app"]
