FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# 纯标准库实现，无需安装第三方依赖
COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

RUN chmod +x scripts/verify.sh && mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import json,os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz', timeout=2)" || exit 1

CMD ["sh", "-c", "python -m app.server"]
