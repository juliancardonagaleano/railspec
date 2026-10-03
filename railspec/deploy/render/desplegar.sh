#!/usr/bin/env sh
# Despliega railspec-server en Render con el Deploy Hook del servicio. Es el mismo código que corre el job
# «render» de .github/workflows/railspec-imagen.yml, para que el camino manual y el de CI no se separen.
#
#   RENDER_DEPLOY_HOOK_URL=<URL del Deploy Hook> RAILSPEC_RENDER_URL=https://<servicio>.onrender.com \
#     sh railspec/deploy/render/desplegar.sh [referencia | --solo-imagen]
#
# Sin argumento construye la imagen del commit actual con docker buildx, la publica en GHCR (hace falta
# `docker login ghcr.io` con un token con write:packages) y despliega su digest. Con una referencia
# (`ghcr.io/<owner>/railspec-server@sha256:…`, la que imprime el workflow railspec-imagen) solo despliega.
# `--solo-imagen` construye y publica sin desplegar ni pedir el hook: es la primera publicación, antes de que
# exista el servicio de Render (el Blueprint necesita la imagen y el Deploy Hook necesita el servicio).
# La URL del Deploy Hook es un secreto: se pasa por el entorno y nunca se imprime.
set -eu

IMAGEN_BASE=${IMAGEN_BASE:-ghcr.io/juliancardonagaleano/railspec-server}
ESPERA_S=${RAILSPEC_RENDER_ESPERA_S:-900}

referencia=${1:-}
if [ -z "$referencia" ] || [ "$referencia" = "--solo-imagen" ]; then
  commit=$(git rev-parse HEAD)
  meta=$(mktemp)
  trap 'rm -f "$meta"' EXIT
  SOURCE_DATE_EPOCH=0 docker buildx build --platform linux/amd64 \
    -f railspec/deploy/servidor/Dockerfile \
    -t "$IMAGEN_BASE:sha-$commit" -t "$IMAGEN_BASE:master" \
    --provenance=false --metadata-file "$meta" --push .
  digest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$meta")
  if [ "${1:-}" = "--solo-imagen" ]; then
    echo "Publicada $IMAGEN_BASE@$digest (etiquetas sha-$commit y master)"
    exit 0
  fi
  referencia="$IMAGEN_BASE@$digest"
fi

: "${RENDER_DEPLOY_HOOK_URL:?falta RENDER_DEPLOY_HOOK_URL (Render > servicio > Settings > Deploy Hook)}"

case "$referencia" in
  "$IMAGEN_BASE"@sha256:????????????????????????????????????????????????????????????????) ;;
  *) echo "referencia inválida: debe ser $IMAGEN_BASE@sha256:<64 hex> (la del servicio, más el digest)" >&2; exit 1 ;;
esac

echo "Desplegando $referencia"
# -G: imgURL va como parámetro de la URL del hook (que ya trae ?key=…); --data-urlencode codifica / y @.
curl -fsS --retry 3 --retry-delay 5 -G "$RENDER_DEPLOY_HOOK_URL" --data-urlencode "imgURL=$referencia" -o /dev/null
echo "Render aceptó el despliegue"

if [ -n "${RAILSPEC_RENDER_URL:-}" ]; then
  # El servicio gratuito se duerme: tras el despliegue arranca en frío. Salud no prueba que ya corra la
  # imagen nueva (la anterior también responde mientras se reemplaza): mira el panel de Render si dudas.
  sleep 45
  url=${RAILSPEC_RENDER_URL%/}
  limite=$(( $(date +%s) + ESPERA_S ))
  while [ "$(date +%s)" -lt "$limite" ]; do
    if curl -fsS --max-time 30 "$url/healthz" >/dev/null 2>&1; then
      echo "$url/healthz responde ok"
      exit 0
    fi
    sleep 15
  done
  echo "$url/healthz no respondió en ${ESPERA_S}s: revisa los registros del servicio en Render" >&2
  exit 1
fi
