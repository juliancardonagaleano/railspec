#!/usr/bin/env sh
# Regenera requirements.lock (dependencias de terceros del binario, con hashes)
# dentro de la misma imagen base que railspec-server (linux/amd64, Python 3.11).
# Sirve también para macOS y Linux arm64: los hashes cubren todas las ruedas
# publicadas de cada versión, y la única dependencia exclusiva de macOS
# (macholib) va fijada sin marcador en requirements.in. Correr desde la raíz del
# repositorio tras cambiar requirements.in o las dependencias de los pyproject
# de railspec-contracts o railspec-local.
set -eu
BASE=$(sed -n 's/^ARG BASE=//p' railspec/deploy/servidor/Dockerfile)
DIR=railspec/packages/railspec-local/empaquetado
docker run --rm --platform linux/amd64 -v "$PWD:/src" -w /src "$BASE" sh -c "
  pip install -q pip-tools==7.6.1 &&
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
    -o $DIR/requirements.lock $DIR/requirements.in"
