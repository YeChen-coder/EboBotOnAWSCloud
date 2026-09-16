FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY realtime-assistant/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY realtime-assistant/app.py .
COPY cloud/runtime.py cloud/healthcheck.py /cloud/
CMD ["python", "-u", "/cloud/runtime.py"]
