#!/usr/bin/env sh
# Regenera requirements.lock con hashes dentro de la misma imagen base que el
# Dockerfile (linux/amd64, Python 3.11), para que los marcadores coincidan con
# los de AKS. Correr desde la raíz del repositorio tras cambiar requirements.in
# o las dependencias de los pyproject de railspec-contracts, railspec-graph o
# railspec-server.
set -eu
BASE=$(sed -n 's/^ARG BASE=//p' railspec/deploy/servidor/Dockerfile)
docker run --rm --platform linux/amd64 -v "$PWD:/src" -w /src "$BASE" sh -c '
  pip install -q pip-tools==7.6.1 &&
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
    -o railspec/deploy/servidor/requirements.lock railspec/deploy/servidor/requirements.in'
