# syntax=docker/dockerfile:1

# ---- Build stage: install the runtime dependencies into /app/deps (exact versions, hashes checked, wheels only)
# Python 3.13 on Debian 13, the same as the runtime image below
FROM public.ecr.aws/docker/library/python:3.13-slim-trixie AS build
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: --target /app/deps -r requirements.txt
COPY orders ./orders

# ---- Runtime stage: distroless = no shell, no package manager, only Python and the app
FROM gcr.io/distroless/python3-debian13:nonroot
WORKDIR /app
# PYTHONDONTWRITEBYTECODE: the root filesystem is read-only in the cluster, so do not try to write .pyc files
ENV PYTHONPATH=/app/deps:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
COPY --from=build --chown=65532:65532 /app /app
# Non-root user (uid 65532 "nonroot"), as required by Pod Security "restricted"
USER 65532:65532
EXPOSE 8080
# The distroless entrypoint is python3
CMD ["-m", "orders.server"]
