FROM python:3.11-slim

WORKDIR /srv

COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/srv/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=2s --retries=10 \
  CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2); assert json.load(r)['status']=='ready'" || exit 1

CMD ["python", "app/server.py"]
