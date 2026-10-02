"""Pruebas del renderizado de manifiestos y del reindexado desde CI."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.snapshot import (
    Arista,
    DeltaIndice,
    MotorIndice,
    Relacion,
    Simbolo,
    TipoSimbolo,
    id_simbolo,
)

DEPLOY = Path(__file__).resolve().parents[1]


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


renderizar = _cargar("railspec_deploy_renderizar", DEPLOY / "renderizar.py")
reindexar = _cargar("railspec_deploy_reindexar", DEPLOY / "ci" / "reindexar.py")

MINIMO = {
    "RAILSPEC_IMAGEN": "registro.azurecr.io/railspec-server@sha256:" + "a" * 64,
    "RAILSPEC_DOMINIO": "railspec.example.com",
    "RAILSPEC_TLS_SECRETO": "railspec-tls",
}

# --- renderizado ----------------------------------------------------------------------


def test_render_sustituye_todo_y_deriva_workload_identity():
    texto = renderizar.renderizar(MINIMO)
    assert "${" not in texto
    assert MINIMO["RAILSPEC_IMAGEN"] in texto
    assert 'azure.workload.identity/use: "false"' in texto
    # El hash de sesión MCP del ingress no es una variable del renderizador.
    assert "$http_mcp_session_id" in texto
    con_id = renderizar.renderizar({**MINIMO, "RAILSPEC_AZURE_CLIENT_ID": "0000-cliente"})
    assert 'azure.workload.identity/use: "true"' in con_id


def test_render_falla_sin_obligatorias():
    with pytest.raises(renderizar.ErrorRender, match="RAILSPEC_IMAGEN"):
        renderizar.renderizar({k: v for k, v in MINIMO.items() if k != "RAILSPEC_IMAGEN"})
    with pytest.raises(renderizar.ErrorRender, match="REPLICAS"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_REPLICAS": "dos"})


def test_render_proveedores_en_configmap():
    yaml = pytest.importorskip("yaml")
    texto = renderizar.renderizar(
        {
            **MINIMO,
            "RAILSPEC_FOUNDRY_REGION": "eastus2",
            "RAILSPEC_FOUNDRY_ZONA_DATOS": "us",
            "RAILSPEC_FOUNDRY_DESPLIEGUES": "opus=claude-opus-5-5:DataZoneStandard,gpt=gpt-5:Standard",
        }
    )
    mapa = next(d for d in yaml.safe_load_all(texto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_FOUNDRY_REGION"] == "eastus2"
    assert mapa["RAILSPEC_FOUNDRY_DESPLIEGUES"].startswith("opus=claude-opus-5-5:DataZoneStandard")
    assert mapa["RAILSPEC_FOUNDRY_PROYECTO"] == ""
    assert mapa["RAILSPEC_CATALOGO_TTL_S"] == "3600"
    assert mapa["RAILSPEC_CACHE_NODOS_S"] == "86400"
    assert mapa["RAILSPEC_CONTEXTO_CACHE_S"] == "900"


def test_render_rechaza_despliegues_json_y_ttl_no_numerico():
    with pytest.raises(renderizar.ErrorRender, match="forma despliegue=modelo"):
        renderizar.renderizar(
            {**MINIMO, "RAILSPEC_FOUNDRY_DESPLIEGUES": '[{"despliegue": "o", "modelo": "claude-opus-5-5"}]'}
        )
    with pytest.raises(renderizar.ErrorRender, match="CATALOGO_TTL_S"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_CATALOGO_TTL_S": "una hora"})


def _configmap(entorno: dict[str, str]) -> dict[str, str]:
    yaml = pytest.importorskip("yaml")
    texto = renderizar.renderizar({**MINIMO, **entorno})
    return next(d for d in yaml.safe_load_all(texto) if d and d["kind"] == "ConfigMap")["data"]


def test_render_oidc_desactivado_por_defecto_y_con_audiencia_vacia():
    """A1: antes ``valor or defecto`` volvía a "railspec" aunque se vaciara la audiencia."""

    assert _configmap({})["RAILSPEC_OIDC_AUDIENCIA"] == ""
    assert _configmap({"RAILSPEC_OIDC_AUDIENCIA": ""})["RAILSPEC_OIDC_AUDIENCIA"] == ""
    assert (
        _configmap({"RAILSPEC_OIDC_AUDIENCIA": "", "RAILSPEC_OIDC_REPOSITORIOS": "a/b"})[
            "RAILSPEC_OIDC_AUDIENCIA"
        ]
        == ""
    )


def test_render_oidc_exige_repositorios_y_una_audiencia_no_adivinable():
    with pytest.raises(renderizar.ErrorRender, match="RAILSPEC_OIDC_REPOSITORIOS"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_OIDC_AUDIENCIA": "una-audiencia-larga-y-aleatoria"})
    with pytest.raises(renderizar.ErrorRender, match="adivinable"):
        renderizar.renderizar(
            {**MINIMO, "RAILSPEC_OIDC_AUDIENCIA": "railspec", "RAILSPEC_OIDC_REPOSITORIOS": "acme/api"}
        )
    mapa = _configmap(
        {
            "RAILSPEC_OIDC_AUDIENCIA": "una-audiencia-larga-y-aleatoria",
            "RAILSPEC_OIDC_REPOSITORIOS": "acme/api,acme/web",
        }
    )
    assert mapa["RAILSPEC_OIDC_AUDIENCIA"] == "una-audiencia-larga-y-aleatoria"
    assert mapa["RAILSPEC_OIDC_REPOSITORIOS"] == "acme/api,acme/web"


def test_deployment_apaga_el_modo_desarrollo_por_encima_del_secret():
    """M4: ``env`` gana a ``envFrom``: una clave sobrante en el Secret no activa los tokens de desarrollo."""

    yaml = pytest.importorskip("yaml")
    documentos = [d for d in yaml.safe_load_all(renderizar.renderizar(MINIMO)) if d]
    contenedor = next(d for d in documentos if d["kind"] == "Deployment")["spec"]["template"]["spec"][
        "containers"
    ][0]
    assert {"secretRef": {"name": "railspec-server"}} in contenedor["envFrom"]
    fijas = {e["name"]: e.get("value") for e in contenedor["env"]}
    assert fijas["RAILSPEC_PERMITIR_DESARROLLO"] == "" and fijas["RAILSPEC_TOKENS_DESARROLLO"] == ""
    # Ni el ConfigMap ni las variables del renderizador pueden encenderlo.
    assert "RAILSPEC_PERMITIR_DESARROLLO" not in renderizar.VARIABLES
    assert "RAILSPEC_TOKENS_DESARROLLO" not in renderizar.VARIABLES
    mapa = next(d for d in documentos if d["kind"] == "ConfigMap")["data"]
    assert "RAILSPEC_PERMITIR_DESARROLLO" not in mapa and "RAILSPEC_TOKENS_DESARROLLO" not in mapa


def test_render_rechaza_variable_desconocida(tmp_path):
    (tmp_path / "x.yaml").write_text("a: ${RAILSPEC_NO_EXISTE}\n")
    with pytest.raises(renderizar.ErrorRender, match="desconocida"):
        renderizar.renderizar(MINIMO, tmp_path)


def test_render_produce_yaml_valido():
    yaml = pytest.importorskip("yaml")
    documentos = [d for d in yaml.safe_load_all(renderizar.renderizar(MINIMO)) if d]
    tipos = sorted(d["kind"] for d in documentos)
    assert tipos == sorted(
        [
            "Namespace",
            "ServiceAccount",
            "ConfigMap",
            "Deployment",
            "Service",
            "Ingress",
            "PodDisruptionBudget",
        ]
    )
    despliegue = next(d for d in documentos if d["kind"] == "Deployment")
    assert despliegue["spec"]["replicas"] == 2


# --- chat y consola -------------------------------------------------------------------


def _documentos(entorno: dict[str, str]) -> list[dict]:
    yaml = pytest.importorskip("yaml")
    return [d for d in yaml.safe_load_all(renderizar.renderizar({**MINIMO, **entorno})) if d]


def _despliegue(entorno: dict[str, str]) -> dict:
    return next(d for d in _documentos(entorno) if d["kind"] == "Deployment")["spec"]["template"]["spec"]


def test_render_chat_y_consola_salen_en_el_configmap_con_los_defectos_del_servidor():
    mapa = _configmap({})
    # Falla cerrado por defecto: sin regiones el chat se niega en restringido e interno.
    assert mapa["RAILSPEC_CHAT_ZONA_DATOS"] == ""
    assert mapa["RAILSPEC_CHAT_MODELO"] == ""  # vacío = el del servidor
    assert mapa["RAILSPEC_CHAT_CLONES"] == ""
    assert {k: v for k, v in mapa.items() if k.startswith("RAILSPEC_CONSOLA_") and k[17:] != "URL"} == {
        "RAILSPEC_CONSOLA_ADMINS": "",
        "RAILSPEC_CONSOLA_SESION_HORAS": "4",
        "RAILSPEC_CONSOLA_AUTH_LIMITE": "60",
        "RAILSPEC_CONSOLA_SSE_MAX_USUARIO": "5",
        "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL": "200",
        "RAILSPEC_CONSOLA_SSE_REVALIDAR_S": "30",
    }
    mapa = _configmap(
        {
            "RAILSPEC_CHAT_ZONA_DATOS": "eastus2, swedencentral",
            "RAILSPEC_CHAT_MODELO": "claude-opus-5-5",
            "RAILSPEC_CONSOLA_SESION_HORAS": "8",
            "RAILSPEC_CONSOLA_AUTH_LIMITE": "0",
            "RAILSPEC_CONSOLA_SSE_MAX_USUARIO": "3",
            "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL": "50",
            "RAILSPEC_CONSOLA_SSE_REVALIDAR_S": "12.5",
        }
    )
    assert mapa["RAILSPEC_CHAT_ZONA_DATOS"] == "eastus2, swedencentral"
    assert mapa["RAILSPEC_CHAT_MODELO"] == "claude-opus-5-5"
    assert mapa["RAILSPEC_CONSOLA_AUTH_LIMITE"] == "0"  # "0" no se pierde ante el defecto
    assert mapa["RAILSPEC_CONSOLA_SSE_REVALIDAR_S"] == "12.5"


def test_render_clones_del_chat_solo_se_montan_con_pvc_y_en_solo_lectura():
    def clones(entorno):
        spec = _despliegue(entorno)
        montaje = next(m for m in spec["containers"][0]["volumeMounts"] if m["name"] == "clones")
        volumen = next(v for v in spec["volumes"] if v["name"] == "clones")
        return montaje, volumen

    montaje, volumen = clones({})
    assert montaje["readOnly"] is True and "persistentVolumeClaim" not in volumen
    assert _configmap({})["RAILSPEC_CHAT_CLONES"] == ""  # sin ruta, el servidor no registra code.read

    entorno = {"RAILSPEC_CHAT_CLONES_PVC": "railspec-clones"}
    montaje, volumen = clones(entorno)
    assert volumen["persistentVolumeClaim"] == {"claimName": "railspec-clones", "readOnly": True}
    assert montaje["readOnly"] is True
    # La ruta que recibe el servidor es la del montaje, no una copia que pueda desviarse.
    assert _configmap(entorno)["RAILSPEC_CHAT_CLONES"] == montaje["mountPath"] == renderizar.RUTA_CLONES
    # El contenedor sigue con la raíz de solo lectura: los clones no la relajan.
    assert _despliegue(entorno)["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True


@pytest.mark.parametrize(
    ("variable", "valor", "texto"),
    [
        ("RAILSPEC_CONSOLA_SESION_HORAS", "0", "SESION_HORAS"),
        ("RAILSPEC_CONSOLA_SESION_HORAS", "25", "SESION_HORAS"),
        ("RAILSPEC_CONSOLA_SESION_HORAS", "cuatro", "SESION_HORAS"),
        ("RAILSPEC_CONSOLA_AUTH_LIMITE", "-1", "AUTH_LIMITE"),
        ("RAILSPEC_CONSOLA_SSE_MAX_USUARIO", "0", "SSE_MAX_USUARIO"),
        ("RAILSPEC_CONSOLA_SSE_MAX_GLOBAL", "1.5", "SSE_MAX_GLOBAL"),
        ("RAILSPEC_CONSOLA_SSE_REVALIDAR_S", "0", "SSE_REVALIDAR_S"),
        ("RAILSPEC_CONSOLA_SSE_REVALIDAR_S", "rápido", "SSE_REVALIDAR_S"),
        ("RAILSPEC_CHAT_ZONA_DATOS", "EastUS2", "minúsculas"),
        ("RAILSPEC_CHAT_ZONA_DATOS", 'eastus2"\n  X: "y', "minúsculas"),
        ("RAILSPEC_CHAT_ZONA_DATOS", "eastus2,", "minúsculas"),
        ("RAILSPEC_CHAT_MODELO", 'claude"\n  X: "y', "CHAT_MODELO"),
        ("RAILSPEC_CHAT_CLONES_PVC", "Clones", "PersistentVolumeClaim"),
        ("RAILSPEC_CHAT_CLONES_PVC", "x}, readOnly: false, {y: 1", "PersistentVolumeClaim"),
    ],
)
def test_render_rechaza_lo_que_el_servidor_no_acepta_o_dejaria_el_chat_mudo(variable, valor, texto):
    with pytest.raises(renderizar.ErrorRender, match=texto):
        renderizar.renderizar({**MINIMO, variable: valor})


def test_avisos_cuando_el_chat_quedaria_sin_responder_o_sin_codigo():
    assert len(renderizar.avisos(MINIMO)) == 2
    completo = {**MINIMO, "RAILSPEC_CHAT_ZONA_DATOS": "eastus2", "RAILSPEC_CHAT_CLONES_PVC": "clones"}
    assert renderizar.avisos(completo) == []
    solo_zona = renderizar.avisos({**MINIMO, "RAILSPEC_CHAT_ZONA_DATOS": "eastus2"})
    assert len(solo_zona) == 1 and "sin leer código" in solo_zona[0]


# --- bases de datos, red, respaldos y clones (opcionales) -----------------------------

CON_PVC = {"RAILSPEC_CHAT_CLONES_PVC": "railspec-clones"}
TODO = {
    "RAILSPEC_MONGO_INTERNO": "true",
    "RAILSPEC_FALKORDB_INTERNO": "true",
    "RAILSPEC_RED_POLITICAS": "true",
    "RAILSPEC_RESPALDO": "true",
    "RAILSPEC_CLONES_CREAR_PVC": "true",
    "RAILSPEC_CLONES_ACTUALIZAR": "true",
    "RAILSPEC_CLONES_REPOSITORIOS": "acme/api, acme/web",
    **CON_PVC,
}
BASE_CI = DEPLOY.parents[1]


def _por_nombre(entorno: dict[str, str]) -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d for d in _documentos(entorno)}


def _plantilla(documento: dict) -> dict:
    """``spec`` del pod, sea Deployment, StatefulSet o CronJob."""

    spec = documento["spec"]
    if documento["kind"] == "CronJob":
        return spec["jobTemplate"]["spec"]["template"]
    return spec["template"]


def test_lo_opcional_no_sale_por_defecto_y_sale_entero_con_las_banderas():
    por_defecto = {(d["kind"], d["metadata"]["name"]) for d in _documentos({})}
    assert not {n for _, n in por_defecto} & {
        "railspec-mongo",
        "railspec-falkordb",
        "railspec-respaldo",
        "railspec-respaldo-mongo",
        "railspec-respaldo-falkordb",
        "railspec-clones",
    }
    todo = _por_nombre(TODO)
    assert set(todo) - por_defecto == {
        ("ConfigMap", "railspec-mongo-init"),
        ("Service", "railspec-mongo"),
        ("StatefulSet", "railspec-mongo"),
        ("Service", "railspec-falkordb"),
        ("StatefulSet", "railspec-falkordb"),
        ("NetworkPolicy", "railspec-server"),
        ("NetworkPolicy", "railspec-mongo"),
        ("NetworkPolicy", "railspec-falkordb"),
        ("PersistentVolumeClaim", "railspec-respaldo"),
        ("CronJob", "railspec-respaldo-mongo"),
        ("CronJob", "railspec-respaldo-falkordb"),
        ("PersistentVolumeClaim", "railspec-clones"),
        ("CronJob", "railspec-clones"),
    }
    # Cada archivo opcional existe y cada entrada de OPCIONALES apunta a uno real.
    assert set(renderizar.OPCIONALES) <= {f.name for f in (DEPLOY / "k8s").glob("*.yaml")}
    assert set(renderizar.BANDERAS) == {b for bs in renderizar.OPCIONALES.values() for b in bs}


def test_cada_base_depende_solo_de_su_bandera():
    solo_mongo = _por_nombre({"RAILSPEC_MONGO_INTERNO": "true", "RAILSPEC_RED_POLITICAS": "true"})
    assert ("StatefulSet", "railspec-mongo") in solo_mongo and (
        "NetworkPolicy",
        "railspec-mongo",
    ) in solo_mongo
    assert not any(n == "railspec-falkordb" for _, n in solo_mongo)
    solo_falkor = _por_nombre({"RAILSPEC_FALKORDB_INTERNO": "true", "RAILSPEC_RESPALDO": "true"})
    assert ("CronJob", "railspec-respaldo-falkordb") in solo_falkor
    assert not any(n in ("railspec-mongo", "railspec-respaldo-mongo") for _, n in solo_falkor)
    # La política del servidor no depende de que haya bases dentro.
    assert ("NetworkPolicy", "railspec-server") in _por_nombre({"RAILSPEC_RED_POLITICAS": "true"})


@pytest.mark.parametrize("nombre", ["railspec-mongo", "railspec-falkordb"])
def test_las_bases_son_un_nodo_con_volumen_imagen_fija_y_credenciales_por_referencia(nombre):
    base = _por_nombre(TODO)[("StatefulSet", nombre)]
    assert base["spec"]["replicas"] == 1 and base["spec"]["serviceName"] == nombre
    plantilla = _plantilla(base)["spec"]
    contenedor = plantilla["containers"][0]
    # Versión y digest, nunca ``latest`` ni una etiqueta móvil.
    assert re.fullmatch(r"[\w./-]+:\d+(\.\d+)+@sha256:[0-9a-f]{64}", contenedor["image"])
    # Sin privilegios: uid fijo distinto de root, raíz de solo lectura y sin capacidades.
    assert (
        plantilla["securityContext"]["runAsNonRoot"] is True and plantilla["securityContext"]["runAsUser"] > 0
    )
    assert contenedor["securityContext"]["readOnlyRootFilesystem"] is True
    assert contenedor["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert contenedor["securityContext"]["allowPrivilegeEscalation"] is False
    # Persistencia activa en un PVC propio; el límite de memoria iguala a la solicitud.
    (plantilla_pvc,) = base["spec"]["volumeClaimTemplates"]
    assert plantilla_pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert plantilla_pvc["spec"]["storageClassName"] == "managed-csi"
    assert any(m["name"] == "data" for m in contenedor["volumeMounts"])
    assert contenedor["resources"]["limits"]["memory"] == contenedor["resources"]["requests"]["memory"]
    # Las credenciales salen de un Secret por referencia: ningún valor secreto en el manifiesto.
    for env in contenedor["env"]:
        if any(p in env["name"] for p in ("PASSWORD", "CLAVE", "AUTH")):
            assert "value" not in env and env["valueFrom"]["secretKeyRef"]["name"] == "railspec-datos"
    for sonda in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert contenedor[sonda]["exec"]["command"]


def test_mongo_crea_un_usuario_de_aplicacion_sin_mas_permisos_que_su_base():
    por_nombre = _por_nombre({**TODO, "RAILSPEC_MONGO_DB": "datos"})
    guion = por_nombre[("ConfigMap", "railspec-mongo-init")]["data"]["10-usuario-aplicacion.js"]
    assert 'roles: [{ role: "readWrite", db: bd }]' in guion and "process.env.RAILSPEC_MONGO_CLAVE" in guion
    assert "root" not in guion and "dbOwner" not in guion
    contenedor = _plantilla(por_nombre[("StatefulSet", "railspec-mongo")])["spec"]["containers"][0]
    env = {e["name"]: e for e in contenedor["env"]}
    assert env["RAILSPEC_MONGO_DB"]["value"] == "datos"
    assert {"MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD", "RAILSPEC_MONGO_CLAVE"} <= set(env)
    assert "--auth" in contenedor["args"]
    montaje = next(m for m in contenedor["volumeMounts"] if m["name"] == "init")
    assert montaje["mountPath"] == "/docker-entrypoint-initdb.d" and montaje["readOnly"] is True


def test_falkordb_exige_contrasena_persiste_y_apaga_el_navegador():
    contenedor = _plantilla(_por_nombre(TODO)[("StatefulSet", "railspec-falkordb")])["spec"]["containers"][0]
    env = {e["name"]: e for e in contenedor["env"]}
    assert env["BROWSER"]["value"] == "0"
    argumentos = env["REDIS_ARGS"]["value"]
    assert "--requirepass $(FALKORDB_PASSWORD)" in argumentos and "--appendonly yes" in argumentos
    # $(VAR) solo se expande con la variable definida antes en la lista.
    nombres = [e["name"] for e in contenedor["env"]]
    assert nombres.index("FALKORDB_PASSWORD") < nombres.index("REDIS_ARGS")
    assert env["REDISCLI_AUTH"]["valueFrom"]["secretKeyRef"]["key"] == "FALKORDB_PASSWORD"
    assert any(m["mountPath"] == "/var/lib/falkordb/data" for m in contenedor["volumeMounts"])


def test_las_politicas_de_red_solo_dejan_pasar_al_servidor_y_al_respaldo():
    por_nombre = _por_nombre({**TODO, "RAILSPEC_RED_INGRESS_NAMESPACE": "ingress-nginx"})
    servidor = por_nombre[("NetworkPolicy", "railspec-server")]["spec"]
    assert servidor["policyTypes"] == ["Ingress"]  # las salidas del servidor quedan abiertas a propósito
    (regla,) = servidor["ingress"]
    assert regla["from"] == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}}}
    ]
    puerto = _plantilla(por_nombre[("Deployment", "railspec-server")])["spec"]["containers"][0]["ports"][0]
    assert regla["ports"] == [{"protocol": "TCP", "port": puerto["containerPort"]}]
    for nombre, puerto_base in (("railspec-mongo", 27017), ("railspec-falkordb", 6379)):
        politica = por_nombre[("NetworkPolicy", nombre)]["spec"]
        assert politica["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": nombre}
        assert politica["policyTypes"] == ["Ingress", "Egress"]
        (entrada,) = politica["ingress"]
        origenes = {o["podSelector"]["matchLabels"]["app.kubernetes.io/name"] for o in entrada["from"]}
        assert origenes == {"railspec-server", "railspec-respaldo"}
        assert all("namespaceSelector" not in o for o in entrada["from"])  # solo pods del mismo namespace
        assert entrada["ports"] == [{"protocol": "TCP", "port": puerto_base}]
        # Sin salida salvo el DNS del clúster.
        (salida,) = politica["egress"]
        assert {p["port"] for p in salida["ports"]} == {53}
        base = _plantilla(por_nombre[("StatefulSet", nombre)])["spec"]["containers"][0]
        assert base["ports"][0]["containerPort"] == puerto_base


def test_las_etiquetas_que_las_politicas_seleccionan_existen_en_los_pods():
    """Una errata en una etiqueta dejaría un pod sin acceso (o una política sin efecto) sin avisar."""

    por_nombre = _por_nombre(TODO)
    etiquetas = {
        "railspec-server": _plantilla(por_nombre[("Deployment", "railspec-server")]),
        "railspec-respaldo": _plantilla(por_nombre[("CronJob", "railspec-respaldo-mongo")]),
    }
    etiquetas_falkor = _plantilla(por_nombre[("CronJob", "railspec-respaldo-falkordb")])
    for nombre, plantilla in etiquetas.items():
        assert plantilla["metadata"]["labels"]["app.kubernetes.io/name"] == nombre
    assert etiquetas_falkor["metadata"]["labels"]["app.kubernetes.io/name"] == "railspec-respaldo"
    for base in ("railspec-mongo", "railspec-falkordb"):
        plantilla = _plantilla(por_nombre[("StatefulSet", base)])
        assert plantilla["metadata"]["labels"]["app.kubernetes.io/name"] == base
        assert por_nombre[("Service", base)]["spec"]["selector"] == {"app.kubernetes.io/name": base}


def test_respaldos_horario_retencion_y_credenciales_por_referencia():
    por_nombre = _por_nombre(
        {
            **TODO,
            "RAILSPEC_RESPALDO_CRON": "30 2 * * 0",
            "RAILSPEC_RESPALDO_RETENCION_DIAS": "14",
            "RAILSPEC_DATOS_SECRETO": "mis-datos",
        }
    )
    volumen = por_nombre[("PersistentVolumeClaim", "railspec-respaldo")]["spec"]
    assert volumen["accessModes"] == ["ReadWriteMany"] and volumen["storageClassName"] == "azurefile-csi"
    for nombre, esperado in (
        ("railspec-respaldo-mongo", "mongodump"),
        ("railspec-respaldo-falkordb", "--rdb"),
    ):
        trabajo = por_nombre[("CronJob", nombre)]
        assert trabajo["spec"]["schedule"] == "30 2 * * 0"
        assert trabajo["spec"]["concurrencyPolicy"] == "Forbid"
        contenedor = _plantilla(trabajo)["spec"]["containers"][0]
        assert esperado in contenedor["args"][0]
        env = {e["name"]: e for e in contenedor["env"]}
        assert env["RETENCION_DIAS"]["value"] == "14"
        secretos = {e["valueFrom"]["secretKeyRef"]["name"] for e in contenedor["env"] if "valueFrom" in e}
        assert secretos == {"mis-datos"}
        assert _plantilla(trabajo)["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == (
            "railspec-respaldo"
        )
        # Primero un temporal y solo después el nombre final: un respaldo a medias nunca parece completo.
        assert ".tmp" in contenedor["args"][0] and 'mv "$archivo.tmp" "$archivo"' in contenedor["args"][0]
    # Los dos trabajos escriben en el mismo volumen: mismo uid, o uno no podría crear su carpeta.
    uids = {
        _plantilla(por_nombre[("CronJob", n)])["spec"]["securityContext"]["runAsUser"]
        for n in ("railspec-respaldo-mongo", "railspec-respaldo-falkordb")
    }
    assert len(uids) == 1


def test_el_respaldo_exige_una_base_interna():
    with pytest.raises(renderizar.ErrorRender, match="RAILSPEC_RESPALDO"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_RESPALDO": "true"})
    renderizar.renderizar({**MINIMO, "RAILSPEC_RESPALDO": "true", "RAILSPEC_FALKORDB_INTERNO": "true"})


def test_clones_el_cronjob_usa_el_pvc_del_chat_la_misma_imagen_y_el_token_por_referencia():
    por_nombre = _por_nombre(
        {**TODO, "RAILSPEC_CLONES_SECRETO": "token-clones", "RAILSPEC_CLONES_CRON": "5 * * * *"}
    )
    trabajo = por_nombre[("CronJob", "railspec-clones")]
    assert trabajo["spec"]["schedule"] == "5 * * * *"
    contenedor = _plantilla(trabajo)["spec"]["containers"][0]
    assert contenedor["image"] == MINIMO["RAILSPEC_IMAGEN"] or contenedor["image"].startswith("registro.")
    assert (
        contenedor["image"]
        == _plantilla(por_nombre[("Deployment", "railspec-server")])["spec"]["containers"][0]["image"]
    )
    env = {e["name"]: e for e in contenedor["env"]}
    assert env["REPOSITORIOS"]["value"] == "acme/api acme/web"  # la coma se vuelve espacio
    assert env["GITHUB_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "token-clones", "key": "GITHUB_TOKEN"}
    assert (
        _plantilla(trabajo)["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "railspec-clones"
    )
    # El PVC creado es el mismo que el servidor monta de solo lectura.
    pvc = por_nombre[("PersistentVolumeClaim", "railspec-clones")]
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]
    volumenes = _plantilla(por_nombre[("Deployment", "railspec-server")])["spec"]["volumes"]
    assert (
        next(v for v in volumenes if v["name"] == "clones")["persistentVolumeClaim"]["claimName"]
        == (pvc["metadata"]["name"])
    )
    # El token no viaja en la línea de comandos ni en la URL.
    assert "GITHUB_TOKEN" in contenedor["args"][0] and "x-access-token:" in contenedor["args"][0]
    assert "https://$GITHUB_TOKEN" not in contenedor["args"][0]


@pytest.mark.parametrize(
    ("entorno", "texto"),
    [
        ({"RAILSPEC_MONGO_INTERNO": "si"}, "RAILSPEC_MONGO_INTERNO debe ser true o false"),
        ({"RAILSPEC_RED_POLITICAS": "True"}, "RAILSPEC_RED_POLITICAS debe ser true o false"),
        ({"RAILSPEC_CLONES_ACTUALIZAR": "true"}, "exige RAILSPEC_CHAT_CLONES_PVC"),
        ({"RAILSPEC_CLONES_CREAR_PVC": "true"}, "exige RAILSPEC_CHAT_CLONES_PVC"),
        ({**CON_PVC, "RAILSPEC_CLONES_ACTUALIZAR": "true"}, "exige RAILSPEC_CLONES_REPOSITORIOS"),
        (
            {**CON_PVC, "RAILSPEC_CLONES_ACTUALIZAR": "true", "RAILSPEC_CLONES_REPOSITORIOS": "../x"},
            "no es un owner/repo válido",
        ),
        (
            {
                **CON_PVC,
                "RAILSPEC_CLONES_ACTUALIZAR": "true",
                "RAILSPEC_CLONES_REPOSITORIOS": "a/b; rm -rf /",
            },
            "no es un owner/repo válido",
        ),
        ({"RAILSPEC_CLONES_REPOSITORIOS": "acme/.."}, "no es un owner/repo válido"),
        ({"RAILSPEC_MONGO_TAMANO": "20GB"}, "RAILSPEC_MONGO_TAMANO"),
        ({"RAILSPEC_FALKORDB_MEMORIA": "2G"}, "RAILSPEC_FALKORDB_MEMORIA"),
        ({"RAILSPEC_RESPALDO_CRON": "cada noche"}, "RAILSPEC_RESPALDO_CRON"),
        ({"RAILSPEC_CLONES_CRON": '* * * * *"\n  x: "y'}, "RAILSPEC_CLONES_CRON"),
        ({"RAILSPEC_RESPALDO_RETENCION_DIAS": "0"}, "RETENCION_DIAS"),
        ({"RAILSPEC_DATOS_SECRETO": "Mi Secreto"}, "RAILSPEC_DATOS_SECRETO"),
        ({"RAILSPEC_DATOS_CLASE": "x\n  y: z"}, "RAILSPEC_DATOS_CLASE"),
        ({"RAILSPEC_RED_INGRESS_NAMESPACE": "a.b"}, "RAILSPEC_RED_INGRESS_NAMESPACE"),
        ({"RAILSPEC_MONGO_IMAGEN": "mongo:7 latest"}, "RAILSPEC_MONGO_IMAGEN"),
    ],
)
def test_render_rechaza_valores_de_datos_que_romperian_el_manifiesto(entorno, texto):
    with pytest.raises(renderizar.ErrorRender, match=texto):
        renderizar.renderizar({**MINIMO, **entorno})


def test_zona_de_datos_del_chat_acepta_la_region_de_un_despliegue_datazone():
    """``renderizar.py`` rechazaba ``zona-us``: un despliegue DataZone no servía al chat en restringido."""

    for valor in ("zona-us", "zona-eu", "zona-us, eastus2", "eastus2,zona-eu", "swedencentral"):
        assert _configmap({"RAILSPEC_CHAT_ZONA_DATOS": valor})["RAILSPEC_CHAT_ZONA_DATOS"] == valor
    for valor in (
        "zona-xx",
        "zona-",
        "Zona-us",
        "zona-US",
        "zona_us",
        "zona-us,",
        "zona-us zona-eu",
        "zona-usa",
    ):
        with pytest.raises(renderizar.ErrorRender, match="zona-us"):
            renderizar.renderizar({**MINIMO, "RAILSPEC_CHAT_ZONA_DATOS": valor})


def test_aviso_si_la_zona_del_chat_no_es_la_del_recurso():
    coincide = {"RAILSPEC_CHAT_ZONA_DATOS": "zona-us", "RAILSPEC_FOUNDRY_ZONA_DATOS": "us", **CON_PVC}
    assert renderizar.avisos({**MINIMO, **coincide}) == []
    otra = renderizar.avisos({**MINIMO, **coincide, "RAILSPEC_FOUNDRY_ZONA_DATOS": "eu"})
    assert len(otra) == 1 and "zona-us" in otra[0] and "RAILSPEC_FOUNDRY_ZONA_DATOS" in otra[0]
    sin_zona = renderizar.avisos({**MINIMO, **coincide, "RAILSPEC_FOUNDRY_ZONA_DATOS": ""})
    assert len(sin_zona) == 1 and "vacía" in sin_zona[0]
    # Una región de Azure ordinaria no se compara con la zona del recurso.
    assert renderizar.avisos({**MINIMO, "RAILSPEC_CHAT_ZONA_DATOS": "eastus2", **CON_PVC}) == []


def test_aviso_de_bases_internas_sin_respaldo():
    sin = renderizar.avisos({**MINIMO, "RAILSPEC_MONGO_INTERNO": "true"})
    assert any("RAILSPEC_RESPALDO" in a for a in sin)
    con = renderizar.avisos({**MINIMO, "RAILSPEC_MONGO_INTERNO": "true", "RAILSPEC_RESPALDO": "true"})
    assert not any("RAILSPEC_RESPALDO" in a for a in con)
    assert not any("RAILSPEC_RESPALDO" in a for a in renderizar.avisos(MINIMO))


def test_las_versiones_de_las_bases_son_las_mismas_en_manifiestos_ci_y_compose_y_ninguna_es_latest():
    ci = (BASE_CI / ".github" / "workflows" / "railspec-ci.yml").read_text(encoding="utf-8")
    compose = (RAIZ / "integracion" / "docker-compose.yml").read_text(encoding="utf-8")
    for imagen in (renderizar.IMAGEN_MONGO, renderizar.IMAGEN_FALKORDB):
        assert re.fullmatch(r"[\w./-]+:\d+(\.\d+)+@sha256:[0-9a-f]{64}", imagen)
        assert f"image: {imagen}" in ci and f"image: {imagen}" in compose
        assert (
            renderizar.VARIABLES[
                "RAILSPEC_MONGO_IMAGEN" if "mongo" in imagen.split(":")[0] else "RAILSPEC_FALKORDB_IMAGEN"
            ][0]
            == imagen
        )
    # Toda ``image:`` de lo que levanta bases (CI, compose, manifiestos con valor literal) lleva versión
    # exacta y digest: ni ``latest`` ni una etiqueta móvil como ``7``.
    for nombre, texto in (
        ("railspec-ci.yml", ci),
        ("docker-compose.yml", compose),
        *((f.name, f.read_text(encoding="utf-8")) for f in (DEPLOY / "k8s").glob("*.yaml")),
    ):
        for imagen in re.findall(r"^\s*image:\s*(\S+)\s*$", texto, re.M):
            if "${" not in imagen:
                assert re.fullmatch(r"[\w./-]+:\d+(\.\d+)+@sha256:[0-9a-f]{64}", imagen), (
                    f"{nombre}: imagen sin versión y digest: {imagen}"
                )


@pytest.mark.skipif(shutil.which("kubeconform") is None, reason="kubeconform no está instalado")
@pytest.mark.parametrize("entorno", [{}, TODO], ids=["defecto", "todo-activado"])
def test_los_manifiestos_validan_contra_el_esquema_de_kubernetes_1_31(entorno, tmp_path):
    archivo = tmp_path / "railspec.yaml"
    archivo.write_text(renderizar.renderizar({**MINIMO, **entorno}), encoding="utf-8")
    salida = subprocess.run(
        ["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.31.0", str(archivo)],
        capture_output=True,
        text=True,
    )
    assert salida.returncode == 0, salida.stdout + salida.stderr


# --- cobertura: ninguna variable del servidor puede quedarse fuera del despliegue ------

RAIZ = DEPLOY.parent
#: Claves del Secret (despliegue.md § Secret): el servidor las lee, pero nunca viajan por el ConfigMap.
CLAVES_DEL_SECRET = {
    "RAILSPEC_MONGO_URI",
    "RAILSPEC_FALKORDB_URL",
    "RAILSPEC_FOUNDRY_API_KEY",
    "RAILSPEC_PCE_API_KEY",
    "RAILSPEC_ANTHROPIC_API_KEY",
    "RAILSPEC_CONSOLA_SECRETO",
    "RAILSPEC_GITHUB_APP_CLIENT_ID",
    "RAILSPEC_GITHUB_APP_CLIENT_SECRET",
}
#: Las que el servidor lee y el despliegue no pasa por el ConfigMap, a propósito.
FUERA_DEL_CONFIGMAP = {
    "RAILSPEC_CONSOLA_DIR": "la imagen la fija con ENV (Dockerfile)",
    "RAILSPEC_SECRETOS_DIR": "ruta de montaje de credencial_ref (proveedores.md)",
    "RAILSPEC_FOUNDRY_PROYECTO_API_VERSION": "versión de la API del proyecto de Foundry (proveedores.md)",
}


def _variables_del_servidor() -> set[str]:
    patron = re.compile(r"\bRAILSPEC_[A-Z0-9]+(?:_[A-Z0-9]+)*\b")
    fuentes = (RAIZ / "packages" / "railspec-server" / "src").rglob("*.py")
    return {nombre for f in fuentes for nombre in patron.findall(f.read_text(encoding="utf-8"))}


def _claves(archivo: str, patron: str) -> set[str]:
    return set(re.findall(patron, (DEPLOY / "k8s" / archivo).read_text(encoding="utf-8"), re.M))


def test_toda_variable_que_lee_el_servidor_esta_en_el_despliegue_o_declarada_fuera():
    """Así quedaron sin salir RAILSPEC_CHAT_* y RAILSPEC_CONSOLA_SSE_*: el chat se negaba sin aviso."""

    en_configmap = _claves("20-configmap.yaml", r"^  (RAILSPEC_[A-Z0-9_]+):")
    en_deployment = _claves("30-deployment.yaml", r"- name: (RAILSPEC_[A-Z0-9_]+)")
    cubiertas = en_configmap | en_deployment | CLAVES_DEL_SECRET | set(FUERA_DEL_CONFIGMAP)
    huerfanas = _variables_del_servidor() - cubiertas
    assert not huerfanas, (
        f"el servidor lee {sorted(huerfanas)} y ni el ConfigMap, ni el Deployment, ni la lista de claves "
        "del Secret ni FUERA_DEL_CONFIGMAP la cubren: añadirla a renderizar.py y despliegue.md"
    )
    # La otra dirección: una errata en el ConfigMap pasaría por válida y el servidor no la leería.
    ignoradas = en_configmap - _variables_del_servidor()
    assert not ignoradas, f"el ConfigMap trae {sorted(ignoradas)} y el servidor no las lee"
    # Y lo que se declara fuera sigue existiendo en el servidor: la lista no puede pudrirse.
    sobrantes = (CLAVES_DEL_SECRET | set(FUERA_DEL_CONFIGMAP)) - _variables_del_servidor()
    assert not sobrantes, f"ya no las lee el servidor: {sorted(sobrantes)}"


def test_despliegue_md_documenta_cada_variable():
    # Las variables de las bases, la red, los respaldos y los clones viven en despliegue-datos.md.
    doc = "".join(
        (RAIZ / "docs" / nombre).read_text(encoding="utf-8")
        for nombre in ("despliegue.md", "despliegue-datos.md")
    )
    sin_fila = [n for n in renderizar.VARIABLES if f"| `{n}` |" not in doc]
    assert not sin_fila, f"faltan en la tabla de despliegue.md o despliegue-datos.md: {sin_fila}"
    docs = "".join(f.read_text(encoding="utf-8") for f in (RAIZ / "docs").glob("*.md"))
    sin_doc = [n for n in sorted(CLAVES_DEL_SECRET | set(FUERA_DEL_CONFIGMAP)) if n not in docs]
    assert not sin_doc, f"sin documentar en railspec/docs: {sin_doc}"


# --- lotes ----------------------------------------------------------------------------

MOTOR = MotorIndice(version="0.11.0")


def _id(i: int) -> str:
    return id_simbolo("repo", "a.py", "funcion", f"f{i}")


def _simbolo(i: int) -> Simbolo:
    return Simbolo(
        id=_id(i),
        nombre=f"f{i}",
        tipo=TipoSimbolo.funcion,
        ruta="a.py",
        linea_inicio=1,
        linea_fin=2,
        sha256="0" * 64,
    )


def test_partir_reparte_por_lista_y_siempre_da_un_lote():
    assert len(reindexar.partir(DeltaIndice(motor=MOTOR), 10)) == 1
    delta = DeltaIndice(
        motor=MOTOR,
        simbolos_upsert=[_simbolo(i) for i in range(25)],
        simbolos_borrados=[_id(99)],
        aristas_agregadas=[Arista(origen=_id(0), destino=_id(1), relacion=Relacion.llama)],
    )
    lotes = reindexar.partir(delta, 10)
    assert [len(x.simbolos_upsert) for x in lotes] == [10, 10, 5]
    assert [len(x.simbolos_borrados) for x in lotes] == [1, 0, 0]
    assert sum(len(x.aristas_agregadas) for x in lotes) == 1


# --- git ------------------------------------------------------------------------------


def _repo(tmp_path: Path) -> tuple[Path, list[str]]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], capture_output=True, check=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "master")
    git("config", "user.email", "ci@example.com")
    git("config", "user.name", "ci")
    commits = []
    for i, archivo in enumerate(["a.py", "b.py", "c.py"]):
        (tmp_path / archivo).write_text(f"def f{i}():\n    return {i}\n")
        git("add", archivo)
        git("commit", "-q", "-m", archivo)
        commits.append(git("rev-parse", "HEAD"))
    return tmp_path, commits


def test_planificar_delta_y_completo(tmp_path):
    raiz, (c1, c2, c3) = _repo(tmp_path)
    delta = reindexar.planificar(raiz, c3, c1)
    assert delta.rutas == ["b.py", "c.py"] and delta.commit_anterior == c1 and delta.base == c1
    for anterior in (None, reindexar.CERO, "f" * 40, c3):
        completo = reindexar.planificar(raiz, c3, anterior)
        assert completo.completo and completo.rutas == ["a.py", "b.py", "c.py"]
    # anterior que no es ancestro (force-push): completo
    assert reindexar.planificar(raiz, c1, c3).completo


def test_planificar_declara_los_commits_cubiertos(tmp_path):
    raiz, (c1, c2, c3) = _repo(tmp_path)
    assert reindexar.planificar(raiz, c3, c1).cubiertos == [c3, c2]  # más reciente primero
    assert reindexar.planificar(raiz, c3, None).cubiertos == [c3, c2, c1]
    assert reindexar.planificar(raiz, c1, c3).cubiertos == [c1]  # force-push: completo de c1


def test_los_commits_cubiertos_siguen_los_primeros_padres_y_tienen_tope(tmp_path, monkeypatch):
    raiz, (c1, c2, c3) = _repo(tmp_path)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(raiz), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("checkout", "-q", "-b", "rama", c1)
    for nombre in ("x.py", "y.py"):
        (raiz / nombre).write_text("x = 1\n")
        git("add", nombre)
        git("commit", "-q", "-m", nombre)
    git("checkout", "-q", "master")
    git("merge", "-q", "--no-ff", "rama", "-m", "merge")
    fusion = git("rev-parse", "HEAD")
    # Las puntas de la rama por defecto son primeros padres: los de ``rama`` no se enumeran.
    assert reindexar.commits_cubiertos(raiz, fusion, c1) == [fusion, c3, c2]
    monkeypatch.setattr(reindexar, "MAX_COMMITS_CUBIERTOS", 2)
    assert reindexar.commits_cubiertos(raiz, fusion, None) == [fusion, c3]  # los más recientes


def test_commits_cubiertos_declara_solo_el_commit_si_git_falla(tmp_path, capsys):
    raiz, (_, _, c3) = _repo(tmp_path)
    assert reindexar.commits_cubiertos(raiz, c3, "f" * 40) == [c3]
    assert "se declara solo el commit" in capsys.readouterr().out


# --- flujo con dobles -----------------------------------------------------------------


class IndexadorDoble:
    def __init__(self) -> None:
        self.llamadas: list[tuple[str, list[str]]] = []

    def delta(self, worktree, repositorio, base_commit, rutas, excluir):
        self.llamadas.append((base_commit, rutas))
        return DeltaIndice(motor=MOTOR, simbolos_upsert=[_simbolo(i) for i in range(len(rutas))])


class ServidorDoble:
    def __init__(self, respuestas: list[tuple[int, dict]] | None = None) -> None:
        self.cuerpos: list[dict] = []
        self.respuestas = list(respuestas or [])

    def __call__(self, url, cuerpo, token):
        assert url == "https://railspec.example.com/v1/tools/graph.index"
        assert token == "jwt"
        self.cuerpos.append(cuerpo)
        if self.respuestas:
            return self.respuestas.pop(0)
        return 200, {
            "commit": cuerpo["commit"],
            "lotes_recibidos": cuerpo["lote"],
            "aplicado": cuerpo["lote"] == cuerpo["lotes"],
        }


ALCANCE = AlcanceRepositorio(org="acme", workspace="ws", repositorio="repo")


def _correr(raiz, commit, anterior, servidor, indexador=None, **extra):
    return reindexar.reindexar(
        raiz,
        ALCANCE,
        "master",
        commit,
        anterior,
        "https://railspec.example.com/",
        lambda: "jwt",
        indexador or IndexadorDoble(),
        [],
        tamano=1,
        transporte=servidor,
        espera_s=0,
        **extra,
    )


def test_reindexar_delta_por_lotes(tmp_path):
    raiz, (c1, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble()
    salida = _correr(raiz, c3, c1, servidor)
    assert salida.aplicado and salida.commit == c3
    assert [(c["lote"], c["lotes"], c["commit_anterior"]) for c in servidor.cuerpos] == [
        (1, 2, c1),
        (2, 2, c1),
    ]
    assert servidor.cuerpos[0]["alcance"] == {"org": "acme", "workspace": "ws", "repositorio": "repo"}


def test_reindexar_declara_los_commits_cubiertos_en_cada_lote(tmp_path):
    raiz, (c1, c2, c3) = _repo(tmp_path)
    servidor = ServidorDoble()
    assert _correr(raiz, c3, c1, servidor).aplicado
    assert [(c["version_contrato"], c["commits_cubiertos"]) for c in servidor.cuerpos] == [
        ("1.5", [c3, c2])
    ] * 2


def test_reindexar_sin_cobertura_habla_1_4(tmp_path):
    """``--sin-cobertura``: sin la lista, el índice completo retira todas las retenidas."""

    raiz, (_, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble()
    assert _correr(raiz, c3, None, servidor, cobertura=False).aplicado
    assert all("commits_cubiertos" not in c and c["version_contrato"] == "1.4" for c in servidor.cuerpos)


def test_un_servidor_14_que_rechaza_la_lista_recibe_el_indice_sin_ella(tmp_path, capsys):
    raiz, (c1, _, c3) = _repo(tmp_path)
    rechazo = {
        "detalle": "entrada fuera de contrato",
        "errores": [{"ruta": "commits_cubiertos", "mensaje": "Extra inputs are not permitted"}],
    }
    servidor = ServidorDoble([(422, rechazo)])
    assert _correr(raiz, c3, c1, servidor).aplicado
    primero, *resto = servidor.cuerpos
    assert "commits_cubiertos" in primero and primero["version_contrato"] == "1.5"
    assert resto and all("commits_cubiertos" not in c and c["version_contrato"] == "1.4" for c in resto)
    assert "sin cobertura" in capsys.readouterr().out


def test_un_422_que_no_es_de_la_cobertura_no_se_reintenta(tmp_path):
    raiz, (c1, _, c3) = _repo(tmp_path)
    ajeno = {"codigo": "snapshot-invalido", "detalle": "lotes incoherentes"}
    servidor = ServidorDoble([(422, ajeno)])
    with pytest.raises(reindexar.RechazoServidor, match="422"):
        _correr(raiz, c3, c1, servidor)
    assert len(servidor.cuerpos) == 1


def test_el_reintento_completo_por_desfase_declara_la_historia_entera(tmp_path):
    raiz, (c1, c2, c3) = _repo(tmp_path)
    servidor = ServidorDoble([(409, {"codigo": "base-commit-distinto", "detalle": "x"})])
    assert _correr(raiz, c3, c1, servidor).aplicado
    assert servidor.cuerpos[0]["commits_cubiertos"] == [c3, c2]
    assert servidor.cuerpos[-1]["commits_cubiertos"] == [c3, c2, c1]
    assert "commit_anterior" not in servidor.cuerpos[-1]


def test_reindexar_reintenta_completo_si_el_canonico_esta_desfasado(tmp_path):
    raiz, (c1, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble(
        [(409, {"codigo": "base-commit-distinto", "detalle": "canónico en otro commit"})]
    )
    indexador = IndexadorDoble()
    salida = _correr(raiz, c3, c1, servidor, indexador)
    assert salida.aplicado
    assert len(indexador.llamadas) == 2 and indexador.llamadas[1][1] == ["a.py", "b.py", "c.py"]
    assert "commit_anterior" not in servidor.cuerpos[-1]


def test_reindexar_reintenta_5xx_y_no_4xx(tmp_path):
    raiz, (_, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble([(503, {}), (503, {})])
    assert _correr(raiz, c3, None, servidor).aplicado
    rechazo = ServidorDoble([(403, {"codigo": "fuera-de-alcance", "detalle": "no"})])
    with pytest.raises(reindexar.RechazoServidor, match="403"):
        _correr(raiz, c3, None, rechazo)
    assert len(rechazo.cuerpos) == 1


def test_token_oidc_exige_permiso():
    with pytest.raises(reindexar.ErrorReindexado, match="id-token"):
        reindexar.TokenOidc("railspec", {})


def test_render_politica_de_contexto_y_vinculos_en_configmap():
    yaml = pytest.importorskip("yaml")
    por_defecto = renderizar.renderizar(MINIMO)
    mapa = next(d for d in yaml.safe_load_all(por_defecto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_VINCULOS_OWNERS"] == ""  # sin allowlist, una organización sin github_org no vincula
    assert (
        mapa["RAILSPEC_PROVEEDORES_HOSTS"] == ""
    )  # sin allowlist, ninguna organización configura proveedores
    texto = renderizar.renderizar({**MINIMO, "RAILSPEC_VINCULOS_OWNERS": "acme, acme-labs"})
    mapa = next(d for d in yaml.safe_load_all(texto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_VINCULOS_OWNERS"] == "acme, acme-labs"
    with pytest.raises(renderizar.ErrorRender, match="VINCULOS_OWNERS"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_VINCULOS_OWNERS": 'acme"\n  X: "y'})
    texto = renderizar.renderizar({**MINIMO, "RAILSPEC_PROVEEDORES_HOSTS": "pce.acme.com, *.mcp.acme.com"})
    mapa = next(d for d in yaml.safe_load_all(texto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_PROVEEDORES_HOSTS"] == "pce.acme.com, *.mcp.acme.com"
    with pytest.raises(renderizar.ErrorRender, match="PROVEEDORES_HOSTS"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_PROVEEDORES_HOSTS": 'pce.acme.com"\n  X: "y'})
