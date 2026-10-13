# NetPulse — Laya (motor de decisiones "System One", alternativa libre a Jev)
# Servicio HTTP que expone /v1/systemone (compatible con el contrato Jev).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf \
    LAYA_HOST=0.0.0.0 \
    LAYA_PORT=8090 \
    LAYA_MODELS=multilingual \
    LAYA_DEFAULT_MODEL=multilingual \
    LAYA_JEV_STRICT=true \
    LAYA_PRELOAD=false

# 1) torch CPU primero: evita que pip arrastre ~1 GB de dependencias CUDA.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# 2) Laya + servidor HTTP
RUN pip install --no-cache-dir "laya[serve]"

VOLUME ["/data/hf"]
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8090/health', timeout=3).status==200 else 1)"

CMD ["laya-serve"]
