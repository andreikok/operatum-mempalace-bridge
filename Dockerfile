# operatum-mempalace-bridge — FastAPI HTTP wrapper around MemPalace.
#
# Single-replica by design. ChromaDB is a single-writer store; running
# two of these against the same palace dir corrupts the HNSW index
# (mempalace 3.3.3 added _pin_hnsw_threads + segment-quarantine to
# defend the single-process case, but multi-process is still
# unsupported). docker-compose enforces replicas=1 and a health-check
# restart-policy — see operatum-ui/gateway/docker-compose.yml.
#
# Build:    docker build -t operatum-mempalace-bridge:0.1.0 .
# Run:      docker run -p 8081:8081 -v palace:/data operatum-mempalace-bridge:0.1.0
# Health:   curl http://localhost:8081/healthz
FROM python:3.12-slim

# ChromaDB pulls onnxruntime; the bundled ONNX model is downloaded on
# first use and cached under /data. Setting these dirs early lets the
# volume mount cover both the palace itself and the embedding cache.
ENV MEMPALACE_PALACE_PATH=/data/palace \
    OPERATUM_BRIDGE_KG_PATH=/data/knowledge_graph.sqlite3 \
    HF_HOME=/data/hf-cache \
    TRANSFORMERS_OFFLINE=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root user. mempalace creates files under /data so we chown after
# the volume mount happens at runtime — but keep the workdir owned by
# the appuser for binary + metadata.
RUN useradd --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Install runtime deps. We deliberately pin in pyproject.toml + use
# pip rather than poetry to keep the image layer count small.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY src/ ./src/

# Pre-create the data dir so the ENTRYPOINT can write to it even if
# the volume isn't mounted (dev convenience).
RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8081

# uvicorn workers=1 is intentional. Multiple workers would each hold
# their own ChromaDB client to the same palace → HNSW corruption. Use
# the docker-compose `replicas: 1` constraint to enforce this at the
# orchestration layer too.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "1"]
