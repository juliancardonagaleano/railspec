# syntax=docker/dockerfile:1.7
# Imagen de railspec-server. Contexto de construcción: raíz del repositorio.
#   docker build -f railspec/deploy/servidor/Dockerfile -t railspec-server .
#
# Reproducible: imagen base fijada por digest, dependencias de terceros
# instaladas desde requirements.lock con --require-hashes y los tres paquetes
# propios como ruedas construidas en la etapa anterior, sin dependencias.
# Para cambiar la base, actualizar el digest y regenerar el lock (bloquear.sh).
ARG BASE=python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

FROM ${BASE} AS ruedas
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 SOURCE_DATE_EPOCH=0
WORKDIR /src
COPY railspec/packages/railspec-contracts railspec-contracts
COPY railspec/packages/railspec-graph railspec-graph
COPY railspec/packages/railspec-server railspec-server
RUN pip wheel --no-deps --wheel-dir /ruedas setuptools==80.9.0 \
 && pip install --no-index --find-links /ruedas setuptools==80.9.0 \
 && pip wheel --no-deps --no-build-isolation --wheel-dir /ruedas \
      ./railspec-contracts ./railspec-graph ./railspec-server

FROM ${BASE}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RAILSPEC_HOST=0.0.0.0 RAILSPEC_PUERTO=8080
COPY railspec/deploy/servidor/requirements.lock /tmp/requirements.lock
RUN --mount=type=bind,from=ruedas,source=/ruedas,target=/ruedas \
    pip install --require-hashes --no-deps -r /tmp/requirements.lock \
 && pip install --no-deps --no-index /ruedas/railspec_*.whl \
 && rm /tmp/requirements.lock \
 && groupadd --gid 10001 railspec \
 && useradd --uid 10001 --gid railspec --no-create-home --home-dir /nonexistent railspec
USER 10001:10001
EXPOSE 8080
# Sin sonda propia: Kubernetes usa GET /healthz (ver railspec/deploy/k8s).
ENTRYPOINT ["railspec-server"]
