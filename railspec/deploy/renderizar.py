"""Renderiza los manifiestos de ``railspec/deploy/k8s`` desde variables de entorno.

    python3 railspec/deploy/renderizar.py > railspec.yaml
    kubectl apply -f railspec.yaml

Solo sustituye ``${VARIABLE}`` de la tabla ``VARIABLES``; cualquier otro
``${...}`` que quede es un error. Falla si falta una variable obligatoria.
Sin dependencias fuera de la biblioteca estándar.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

DIRECTORIO = Path(__file__).resolve().parent / "k8s"

#: nombre -> (valor por defecto o None si es obligatoria, descripción)
VARIABLES: dict[str, tuple[str | None, str]] = {
    "RAILSPEC_IMAGEN": (
        None,
        "Imagen del servidor, idealmente por digest (registro/railspec-server@sha256:…).",
    ),
    "RAILSPEC_DOMINIO": (None, "Host público del ingress (p. ej. railspec.midominio.com)."),
    "RAILSPEC_TLS_SECRETO": (None, "Secret TLS (kubernetes.io/tls) del dominio en el namespace."),
    "RAILSPEC_NAMESPACE": ("railspec", "Namespace de Kubernetes."),
    "RAILSPEC_SECRETO": ("railspec-server", "Secret con RAILSPEC_MONGO_URI y demás claves."),
    "RAILSPEC_INGRESS_CLASE": ("webapprouting.kubernetes.azure.com", "IngressClass NGINX."),
    "RAILSPEC_REPLICAS": ("2", "Réplicas del Deployment."),
    "RAILSPEC_CPU_SOLICITUD": ("250m", "CPU solicitada por réplica (sin límite de CPU)."),
    "RAILSPEC_MEMORIA": ("1Gi", "Memoria solicitada y límite por réplica."),
    "RAILSPEC_MONGO_DB": ("railspec", "Base de datos de Mongo."),
    "RAILSPEC_FOUNDRY_ENDPOINT": ("", "Endpoint del recurso de Azure AI Foundry."),
    "RAILSPEC_FOUNDRY_REGION": ("", "Región del recurso de Foundry (p. ej. eastus2)."),
    "RAILSPEC_FOUNDRY_ZONA_DATOS": ("", "Zona de datos del recurso de Foundry (us o eu)."),
    "RAILSPEC_FOUNDRY_PROYECTO": ("", "Endpoint del proyecto de Foundry para leer los despliegues."),
    "RAILSPEC_FOUNDRY_DESPLIEGUES": (
        "",
        "Despliegues declarados, despliegue=modelo[:SKU] separados por comas.",
    ),
    "RAILSPEC_CATALOGO_TTL_S": ("3600", "Vigencia del catálogo de modelos, en segundos."),
    "RAILSPEC_CACHE_NODOS_S": ("86400", "Caché de nodos de modelo por hash de entradas (s); 0 la apaga."),
    "RAILSPEC_PCE_URL": ("", "URL MCP de la gobernanza (PCE)."),
    "RAILSPEC_CONTEXTO_CACHE_S": ("900", "Caché de consultas a proveedores de contexto, en segundos."),
    "RAILSPEC_PROVEEDORES_HOSTS": (
        "",
        "Hosts permitidos (coma, *.dominio) para proveedores de contexto de las organizaciones.",
    ),
    "RAILSPEC_VINCULOS_OWNERS": (
        "",
        "Owners de GitHub (coma) que vincula una organización sin github_org; vacío = ninguno.",
    ),
    "RAILSPEC_ANTHROPIC_HABILITADO": ("false", "Anthropic directo (solo nivel abierto)."),
    "RAILSPEC_AZURE_CLIENT_ID": ("", "Client id de la identidad administrada para Workload Identity."),
    "RAILSPEC_OIDC_AUDIENCIA": ("railspec", "Audiencia del token OIDC de CI; la del workflow de reindexado."),
    "RAILSPEC_OIDC_EMISOR": ("https://token.actions.githubusercontent.com", "Emisor OIDC de CI."),
    "RAILSPEC_OIDC_REPOSITORIOS": ("", "owner/repo separados por comas que pueden llamar graph.index."),
    "RAILSPEC_CONSOLA_ADMINS": ("", "github_id que administran la plataforma en la consola (coma)."),
}
_MARCA = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ErrorRender(ValueError):
    pass


def valores(entorno: Mapping[str, str]) -> dict[str, str]:
    salida: dict[str, str] = {}
    faltan = []
    for nombre, (defecto, _) in VARIABLES.items():
        valor = entorno.get(nombre) or defecto
        if valor is None:
            faltan.append(nombre)
        else:
            salida[nombre] = valor
    if faltan:
        raise ErrorRender("faltan variables obligatorias: " + ", ".join(faltan))
    if not salida["RAILSPEC_REPLICAS"].isdigit():
        raise ErrorRender("RAILSPEC_REPLICAS debe ser un entero")
    for nombre in ("RAILSPEC_IMAGEN", "RAILSPEC_DOMINIO", "RAILSPEC_NAMESPACE", "RAILSPEC_SECRETO"):
        if re.search(r"[\s\"'$]", salida[nombre]):
            raise ErrorRender(f"{nombre} tiene caracteres no válidos")
    for nombre in ("RAILSPEC_CATALOGO_TTL_S", "RAILSPEC_CACHE_NODOS_S", "RAILSPEC_CONTEXTO_CACHE_S"):
        if not re.fullmatch(r"\d+(\.\d+)?", salida[nombre]):
            raise ErrorRender(f"{nombre} debe ser un número de segundos")
    for nombre in ("RAILSPEC_VINCULOS_OWNERS",):
        if not re.fullmatch(r"[A-Za-z0-9,\s-]*", salida[nombre]):
            raise ErrorRender(f"{nombre} solo admite owners de GitHub separados por comas")
    if not re.fullmatch(r"[A-Za-z0-9.*,\s-]*", salida["RAILSPEC_PROVEEDORES_HOSTS"]):
        raise ErrorRender(
            "RAILSPEC_PROVEEDORES_HOSTS solo admite hosts (nombre o *.dominio) separados por comas"
        )
    if re.search(r"[\"\\]", salida["RAILSPEC_FOUNDRY_DESPLIEGUES"]):
        raise ErrorRender(
            "RAILSPEC_FOUNDRY_DESPLIEGUES va entre comillas en el ConfigMap: "
            "usar la forma despliegue=modelo[:SKU], no JSON"
        )
    salida["RAILSPEC_WORKLOAD_IDENTITY"] = "true" if salida["RAILSPEC_AZURE_CLIENT_ID"] else "false"
    return salida


def renderizar(entorno: Mapping[str, str], directorio: Path = DIRECTORIO) -> str:
    tabla = valores(entorno)
    documentos = []
    for archivo in sorted(directorio.glob("*.yaml")):
        texto = archivo.read_text(encoding="utf-8")

        def sustituir(m: re.Match[str], archivo: Path = archivo) -> str:
            if m.group(1) not in tabla:
                raise ErrorRender(f"{archivo.name}: variable desconocida {m.group(0)}")
            return tabla[m.group(1)]

        documentos.append(f"# Fuente: {archivo.name}\n" + _MARCA.sub(sustituir, texto).strip() + "\n")
    return "---\n".join(documentos)


def main() -> int:
    if "--variables" in sys.argv[1:]:
        for nombre, (defecto, descripcion) in VARIABLES.items():
            marca = "obligatoria" if defecto is None else f"defecto {defecto!r}"
            print(f"{nombre}\t{marca}\t{descripcion}")
        return 0
    try:
        sys.stdout.write(renderizar(os.environ))
    except ErrorRender as exc:
        print(f"renderizar: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
