FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/agora/agora_sdk
RUN apt-get update && apt-get install -y --no-install-recommends jq curl ca-certificates procps mosquitto ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --retries 5 --timeout 60 agora-python-server-sdk==2.4.9 paho-mqtt==2.1.0 cryptography==50.0.1 segno==1.6.6 websockets==17.1
ARG MEDIAMTX_VERSION=1.9.3
RUN curl -fsSL --retry 5 "https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_amd64.tar.gz" -o /tmp/mediamtx.tar.gz && tar xzf /tmp/mediamtx.tar.gz -C /usr/local/bin mediamtx && rm /tmp/mediamtx.tar.gz
WORKDIR /app
COPY ha-enabot/ebo/*.py ha-enabot/ebo/config.yaml ha-enabot/ebo/run.sh /app/
COPY cloud/runtime.py cloud/healthcheck.py /cloud/
RUN sed -i 's/\r$//' /app/run.sh && chmod +x /app/run.sh && python -c "import re,pathlib; s=pathlib.Path('config.yaml').read_text(); pathlib.Path('VERSION.txt').write_text(re.search(r'^version:\\s*[\"\x27]?([^\"\x27\\n]+)',s,re.M).group(1))"
CMD ["python", "-u", "/cloud/runtime.py"]
