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

#: Dónde monta el Deployment los clones del chat; ``RAILSPEC_CHAT_CLONES`` apunta aquí.
RUTA_CLONES = "/var/lib/railspec/clones"

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
    # El defecto es vacío a propósito: audiencia vacía = OIDC de CI desactivado. Nunca poner aquí un
    # valor por defecto: ``valores`` usa ``valor or defecto`` y vaciarla no podría desactivarlo.
    "RAILSPEC_OIDC_AUDIENCIA": (
        "",
        "Audiencia del token OIDC de CI (valor largo y aleatorio, no adivinable); la del workflow de "
        "reindexado. Vacía = OIDC desactivado.",
    ),
    "RAILSPEC_OIDC_EMISOR": ("https://token.actions.githubusercontent.com", "Emisor OIDC de CI."),
    "RAILSPEC_OIDC_REPOSITORIOS": (
        "",
        "owner/repo separados por comas que pueden llamar graph.index. Obligatoria con audiencia.",
    ),
    "RAILSPEC_CONSOLA_ADMINS": ("", "github_id que administran la plataforma en la consola (coma)."),
    "RAILSPEC_CONSOLA_SESION_HORAS": ("4", "Vida de la sesión de la consola, en horas (1 a 24)."),
    "RAILSPEC_CONSOLA_AUTH_LIMITE": (
        "60",
        "Peticiones por minuto y por IP en /consola/api/auth/* (0 = sin límite).",
    ),
    "RAILSPEC_CONSOLA_SSE_MAX_USUARIO": ("5", "Flujos de eventos en vivo por persona y réplica."),
    "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL": ("200", "Flujos de eventos en vivo por réplica."),
    "RAILSPEC_CONSOLA_SSE_REVALIDAR_S": (
        "30",
        "Cada cuántos segundos un flujo en vivo vuelve a comprobar el rol lector.",
    ),
    # Vacía a propósito: el chat falla cerrado. Sin regiones aquí no responde en restringido ni interno.
    "RAILSPEC_CHAT_ZONA_DATOS": (
        "",
        "Regiones de Azure (coma, minúsculas: eastus2) donde el chat puede enviar código en "
        "restringido/interno. Vacía = el chat no responde en esos niveles.",
    ),
    "RAILSPEC_CHAT_MODELO": ("", "Despliegue de Foundry del rol chat. Vacío = claude-sonnet-5-5."),
    "RAILSPEC_CHAT_CLONES_PVC": (
        "",
        "PersistentVolumeClaim con un clon por repositorio (<owner>/<repo>), que se monta de solo lectura "
        "en " + RUTA_CLONES + ". Vacío = el chat responde sin leer código.",
    ),
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
    _validar_oidc(salida)
    _validar_consola(salida)
    _validar_chat(salida)
    salida["RAILSPEC_WORKLOAD_IDENTITY"] = "true" if salida["RAILSPEC_AZURE_CLIENT_ID"] else "false"
    # Derivadas: el volumen de clones solo existe con PVC; sin él el servidor no recibe ruta y no
    # registra ``code.read`` (el chat responde sin leer código).
    pvc = salida["RAILSPEC_CHAT_CLONES_PVC"]
    salida["RAILSPEC_CHAT_CLONES_RUTA"] = RUTA_CLONES
    salida["RAILSPEC_CHAT_CLONES"] = RUTA_CLONES if pvc else ""
    volumen = "emptyDir: {sizeLimit: 1Mi}"
    if pvc:
        volumen = f"persistentVolumeClaim: {{claimName: {pvc}, readOnly: true}}"
    salida["RAILSPEC_CHAT_CLONES_VOLUMEN"] = volumen
    return salida


def _entero(salida: Mapping[str, str], nombre: str, minimo: int, maximo: int | None = None) -> None:
    valor = salida[nombre]
    if not re.fullmatch(r"\d+", valor) or int(valor) < minimo or (maximo is not None and int(valor) > maximo):
        rango = f"de {minimo} a {maximo}" if maximo is not None else f">= {minimo}"
        raise ErrorRender(f"{nombre} debe ser un entero {rango} (el servidor no arranca con otro valor)")


def _validar_consola(salida: Mapping[str, str]) -> None:
    """Los mismos límites que ``ConfigConsola.desde_entorno``: un valor inválido tumba el arranque."""

    _entero(salida, "RAILSPEC_CONSOLA_SESION_HORAS", 1, 24)
    _entero(salida, "RAILSPEC_CONSOLA_AUTH_LIMITE", 0)
    _entero(salida, "RAILSPEC_CONSOLA_SSE_MAX_USUARIO", 1)
    _entero(salida, "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL", 1)
    revalidar = salida["RAILSPEC_CONSOLA_SSE_REVALIDAR_S"]
    if not re.fullmatch(r"\d+(\.\d+)?", revalidar) or float(revalidar) <= 0:
        raise ErrorRender("RAILSPEC_CONSOLA_SSE_REVALIDAR_S debe ser un número de segundos mayor que cero")


def _validar_chat(salida: Mapping[str, str]) -> None:
    # El servidor compara cada región con la del despliegue tal cual (minúsculas): una mayúscula
    # no falla al arrancar, hace que el chat se niegue en silencio.
    if not re.fullmatch(r"[a-z0-9]+(\s*,\s*[a-z0-9]+)*|", salida["RAILSPEC_CHAT_ZONA_DATOS"]):
        raise ErrorRender(
            "RAILSPEC_CHAT_ZONA_DATOS espera regiones de Azure en minúsculas separadas por comas "
            "(p. ej. eastus2,swedencentral)"
        )
    if not re.fullmatch(r"[A-Za-z0-9._:-]*", salida["RAILSPEC_CHAT_MODELO"]):
        raise ErrorRender("RAILSPEC_CHAT_MODELO solo admite letras, dígitos y . _ : -")
    pvc = salida["RAILSPEC_CHAT_CLONES_PVC"]
    if pvc and not re.fullmatch(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?", pvc):
        raise ErrorRender("RAILSPEC_CHAT_CLONES_PVC debe ser un nombre de PersistentVolumeClaim válido")


def avisos(entorno: Mapping[str, str]) -> list[str]:
    """Configuraciones válidas que dejan el chat sin responder o sin código; van a stderr."""

    tabla = valores(entorno)
    salida = []
    if not tabla["RAILSPEC_CHAT_ZONA_DATOS"]:
        salida.append(
            "RAILSPEC_CHAT_ZONA_DATOS está vacía: el chat no responderá en restringido ni interno "
            "(falla cerrado; ver railspec/docs/chat.md)"
        )
    if not tabla["RAILSPEC_CHAT_CLONES_PVC"]:
        salida.append("RAILSPEC_CHAT_CLONES_PVC está vacía: el chat responderá sin leer código")
    return salida


def _validar_oidc(salida: Mapping[str, str]) -> None:
    """El mismo rechazo que hace el servidor al arrancar, pero antes de aplicar nada al clúster."""

    audiencia = salida["RAILSPEC_OIDC_AUDIENCIA"]
    repositorios = salida["RAILSPEC_OIDC_REPOSITORIOS"]
    for nombre, valor in (
        ("RAILSPEC_OIDC_AUDIENCIA", audiencia),
        ("RAILSPEC_OIDC_REPOSITORIOS", repositorios),
    ):
        if re.search(r"[\s\"\\$]", valor.replace(",", "")):
            raise ErrorRender(f"{nombre} tiene caracteres no válidos")
    if not audiencia:
        return
    if audiencia.lower() == "railspec":
        raise ErrorRender(
            "RAILSPEC_OIDC_AUDIENCIA=railspec es adivinable: usa un valor largo y aleatorio "
            "(p. ej. openssl rand -hex 24) y el mismo en la variable del repositorio del workflow"
        )
    if not any(r.strip() for r in repositorios.split(",")):
        raise ErrorRender(
            "RAILSPEC_OIDC_AUDIENCIA exige RAILSPEC_OIDC_REPOSITORIOS (owner/repo separados por comas): "
            "sin la lista, cualquier repositorio de GitHub podría presentar un token"
        )


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
        for aviso in avisos(os.environ):
            print(f"renderizar: aviso: {aviso}", file=sys.stderr)
    except ErrorRender as exc:
        print(f"renderizar: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
