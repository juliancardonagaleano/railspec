"""Instancias válidas de cada mensaje, compartidas por pruebas y ejemplos.

``python railspec/tests/fabricas.py`` regenera ``railspec/examples/v1``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from railspec.contracts.chat import (
    Afirmacion,
    ConsumoFuga,
    Conversacion,
    EvaluacionRegla,
    LlamadaTool,
    MensajeChat,
    ReglaGate,
    RespuestaChat,
    ResultadoRegla,
    RolMensaje,
    VeredictoGateSalida,
)
from railspec.contracts.comun import (
    Actor,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    Arnes,
    Canal,
    Criterio,
    EstadoFase,
    Fase,
    GateFase,
    GobernanzaConsultada,
    Modo,
    NivelCodigo,
    OidcGithubActions,
    Perfil,
    Presupuesto,
    Proveedor,
    Riesgo,
    RolRepositorio,
    TipoActor,
    Veredicto,
)
from railspec.contracts.estado import (
    EstadoLocal,
    EstadoUnidad,
    RepositorioUnidad,
    ResultadoGate,
)
from railspec.contracts.eventos import Direccion, EventoSync, SnapshotSubido
from railspec.contracts.insumo import Insumo, RepositorioInsumo
from railspec.contracts.orden import AlcanceArchivos, ContextoArmado, OrdenImplementar, Tarea
from railspec.contracts.referencias import RefCriterio, RefSimbolo
from railspec.contracts.reporte import ReporteOrden, ResultadoOrden, ResultadoValidacion
from railspec.contracts.repositorio import (
    AsignacionRol,
    Auditoria,
    Capacidades,
    EventoAuditoria,
    ModeloCatalogo,
    Organizacion,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
    RegistroAuditoria,
    RequisitoRol,
    Rol,
    SujetoEquipo,
    TelemetriaNodo,
    TopeGate,
    VinculoRepositorio,
    Workspace,
    politica_chat_por_defecto,
)
from railspec.contracts.snapshot import (
    CambioArchivo,
    DeltaIndice,
    Embedding,
    EscaneoSecretos,
    EstadoArchivo,
    ModoDelta,
    MotorIndice,
    Simbolo,
    Snapshot,
    TipoSimbolo,
)

T0 = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
BASE = "4063ae9" + "0" * 33
ARBOL = "a" * 40


def uid(n: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{n:012d}")


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


ALCANCE_WS = AlcanceWorkspace(org="acme", workspace="certificados")
ALCANCE_REPO = AlcanceRepositorio(org="acme", workspace="certificados", repositorio="certificados-api")
ALCANCE_UNIDAD = AlcanceUnidad(org="acme", workspace="certificados", unidad="0001-emitir-pdf")

JULIAN = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=83125327, login="juliancardonagaleano")
JULIAN_WEB = JULIAN.model_copy(update={"canal": Canal.consola})
SERVIDOR = Actor(tipo=TipoActor.agente, canal=Canal.servidor, agente="motor")
CI = Actor(
    tipo=TipoActor.servicio,
    canal=Canal.ci,
    oidc=OidcGithubActions(
        repositorio="acme/certificados-api",
        workflow="acme/certificados-api/.github/workflows/ci.yml@refs/heads/main",
    ),
)
AUDITORIA = Auditoria(creado_por=JULIAN_WEB, creado_en=T0, actualizado_por=JULIAN_WEB, actualizado_en=T0)

SIMBOLO_ID = sha("certificados-api\0src/pdf.py\0funcion\0pdf.emitir")


def orden() -> OrdenImplementar:
    return OrdenImplementar(
        id=uid(1),
        secuencia=4,
        unidad=ALCANCE_UNIDAD,
        repositorio="certificados-api",
        base_commit=BASE,
        fase=Fase.implement,
        emitida_en=T0,
        expira_en=T0 + timedelta(hours=8),
        instrucciones="Implementa el grupo G1 del plan: emisión del PDF firmado.",
        contexto=ContextoArmado(gobernanza_consultada=GobernanzaConsultada.si),
        alcance=AlcanceArchivos(permitidos=["src/pdf.py", "tests/test_pdf.py"]),
        criterios=[Criterio(id="CA-01", texto="El PDF emitido lleva firma verificable.")],
        comando_validacion="pytest tests/test_pdf.py",
        grupo="G1",
        tareas=[Tarea(id="T-01", descripcion="Firmar el PDF al emitir.", criterios=["CA-01"])],
    )


def snapshot() -> Snapshot:
    return Snapshot(
        id=uid(2),
        unidad=ALCANCE_UNIDAD,
        repositorio="certificados-api",
        base_commit=BASE,
        hash_arbol=ARBOL,
        creado_en=T0 + timedelta(hours=1),
        nivel_codigo=NivelCodigo.restringido,
        modo_delta=ModoDelta.completo,
        archivos=[
            CambioArchivo(
                ruta="src/pdf.py",
                estado=EstadoArchivo.modificado,
                sha256_antes=sha("antes"),
                sha256_despues=sha("despues"),
            )
        ],
        delta_indice=DeltaIndice(
            motor=MotorIndice(version="0.11.0"),
            simbolos_upsert=[
                Simbolo(
                    id=SIMBOLO_ID,
                    nombre="pdf.emitir",
                    tipo=TipoSimbolo.funcion,
                    ruta="src/pdf.py",
                    linea_inicio=10,
                    linea_fin=42,
                    sha256=sha("def emitir(): ..."),
                )
            ],
            embeddings=[Embedding(simbolo=SIMBOLO_ID, vector_b64=base64.b64encode(bytes(768)).decode())],
        ),
        escaneo_secretos=EscaneoSecretos(herramienta="gitleaks", version="8.21.0", hallazgos=0),
    )


def reporte() -> ReporteOrden:
    return ReporteOrden(
        orden_id=uid(1),
        secuencia=4,
        unidad=ALCANCE_UNIDAD,
        base_commit=BASE,
        resultado=ResultadoOrden.completado,
        reportado_en=T0 + timedelta(hours=1),
        snapshot=snapshot(),
        validacion=ResultadoValidacion(
            comando="pytest tests/test_pdf.py", codigo_salida=0, duracion_ms=5321, salida="3 passed"
        ),
        tareas_completadas=["T-01"],
    )


def evento() -> EventoSync:
    return EventoSync(
        id=uid(3),
        direccion=Direccion.local_a_remoto,
        unidad=ALCANCE_UNIDAD,
        secuencia=7,
        emitido_en=T0 + timedelta(hours=1),
        actor=JULIAN,
        carga=SnapshotSubido(
            snapshot_id=uid(2), repositorio="certificados-api", base_commit=BASE, hash_arbol=ARBOL
        ),
    )


def estado_unidad() -> EstadoUnidad:
    return EstadoUnidad(
        unidad=ALCANCE_UNIDAD,
        version=12,
        titulo="Emitir certificados en PDF firmado",
        dueno=JULIAN,
        arnes=Arnes.claude_code,
        repositorios=[
            RepositorioUnidad(
                repositorio="certificados-api", rol=RolRepositorio.primario, rama="rs/0001", base_commit=BASE
            ),
            RepositorioUnidad(repositorio="reporteria", rol=RolRepositorio.transversal, base_commit="b" * 40),
        ],
        fase=Fase.implement,
        estado=EstadoFase.en_progreso,
        modo=Modo.interactivo,
        riesgo=Riesgo.medio,
        perfil=Perfil.estandar,
        governance_refs=["ADR-012"],
        comando_validacion="pytest",
        insumos=[uid(9)],
        gates={
            GateFase.spec: ResultadoGate(
                veredicto=Veredicto.refinado,
                iteraciones=1,
                gobernanza_consultada=GobernanzaConsultada.si,
                criticos=["correctitud"],
                cerrado_en=T0 - timedelta(hours=3),
            )
        },
        orden_vigente=uid(1),
        secuencia_ordenes=4,
        creado_en=T0 - timedelta(hours=5),
        actualizado_en=T0,
        actualizado_por=SERVIDOR,
    )


def estado_local() -> EstadoLocal:
    return EstadoLocal(
        unidad=ALCANCE_UNIDAD,
        repositorio="certificados-api",
        worktree="/home/dev/certificados-api/.railspec/worktrees/0001-emitir-pdf",
        rama="rs/0001",
        base_commit=BASE,
        espejo_remoto=estado_unidad(),
        orden_en_curso=orden(),
        ultima_secuencia_recibida=20,
        ultima_secuencia_confirmada=6,
        cola_pendiente=[evento()],
    )


def veredicto_permitido() -> VeredictoGateSalida:
    return VeredictoGateSalida(
        version_gate="1.0.0",
        permitido=True,
        reglas=[EvaluacionRegla(regla=r, resultado=ResultadoRegla.pasa) for r in ReglaGate],
        evaluado_en=T0,
    )


def respuesta_chat() -> RespuestaChat:
    return RespuestaChat(
        afirmaciones=[
            Afirmacion(
                texto="La emisión del PDF vive en pdf.emitir y no firma el documento.",
                referencias=[
                    RefSimbolo(
                        repositorio="certificados-api",
                        commit=BASE,
                        simbolo=SIMBOLO_ID,
                        nombre="pdf.emitir",
                        tipo_simbolo=TipoSimbolo.funcion,
                        ruta="src/pdf.py",
                    )
                ],
            )
        ]
    )


def mensaje_chat() -> MensajeChat:
    return MensajeChat(
        id=uid(5),
        conversacion=uid(4),
        alcance=ALCANCE_WS,
        rol=RolMensaje.asistente,
        autor=Actor(tipo=TipoActor.agente, canal=Canal.consola, agente="chat", en_nombre_de=83125327),
        creado_en=T0,
        respuesta=respuesta_chat(),
        veredicto_gate=veredicto_permitido(),
        llamadas_tool=[LlamadaTool(tool="graph.query", entrada_sha256=sha("q"), duracion_ms=120)],
        proveedor=Proveedor.foundry,
        modelo="claude-sonnet-5-5",
        conservar_en_insumo=True,
    )


def conversacion() -> Conversacion:
    return Conversacion(
        id=uid(4),
        alcance=ALCANCE_WS,
        repositorios=["certificados-api", "reporteria"],
        autor=JULIAN_WEB,
        nivel_efectivo=NivelCodigo.restringido,
        creada_en=T0,
        expira_en=T0 + timedelta(days=30),
        consumo_fuga=ConsumoFuga(caracteres=120, tope=1500),
        consumo_fuga_usuario=ConsumoFuga(caracteres=480, tope=6000),
    )


def insumo() -> Insumo:
    datos: dict[str, Any] = dict(
        id=uid(9),
        alcance=ALCANCE_WS,
        repositorios=[
            RepositorioInsumo(repositorio="certificados-api", rol=RolRepositorio.primario, base_commit=BASE)
        ],
        autor=JULIAN_WEB,
        creado_en=T0,
        nivel_efectivo=NivelCodigo.restringido,
        conversacion=uid(4),
        objetivo="Firmar los certificados PDF al emitirlos.",
        hallazgos=respuesta_chat().afirmaciones
        + [
            Afirmacion(
                texto="El criterio de firma verificable ya existe en una unidad previa.",
                referencias=[
                    RefCriterio(workspace="certificados", unidad="0001-emitir-pdf", criterio="CA-01")
                ],
            )
        ],
        restricciones=["No cambiar el formato del número de certificado."],
        veredicto_gate=veredicto_permitido(),
    )
    provisional = Insumo.model_construct(**datos, sha256="0" * 64)
    return Insumo(**datos, sha256=hashlib.sha256(provisional.contenido_canonico()).hexdigest())


def organizacion() -> Organizacion:
    return Organizacion(
        version=1, auditoria=AUDITORIA, id="acme", nombre="Acme", github_org="acme", region_datos="eastus2"
    )


def workspace() -> Workspace:
    return Workspace(
        version=3, auditoria=AUDITORIA, alcance=ALCANCE_WS, nombre="Certificados", zona_datos_azure="us"
    )


def asignacion_rol() -> AsignacionRol:
    return AsignacionRol(
        version=1,
        auditoria=AUDITORIA,
        id=uid(6),
        org="acme",
        workspace="certificados",
        rol=Rol.desarrollador,
        sujeto=SujetoEquipo(github_org="acme", equipo="certificados-dev", equipo_id=4242),
    )


def vinculo() -> VinculoRepositorio:
    return VinculoRepositorio(
        version=2,
        auditoria=AUDITORIA,
        alcance=ALCANCE_REPO,
        url="https://github.com/acme/certificados-api",
        rol=RolRepositorio.primario,
        chat_contexto_codigo=politica_chat_por_defecto(NivelCodigo.restringido),
    )


def modelo_catalogo() -> ModeloCatalogo:
    return ModeloCatalogo(
        org="acme",
        proveedor=Proveedor.foundry,
        modelo="claude-sonnet-5-5",
        despliegue="sonnet-55-eastus2",
        hosting="azure",
        region="eastus2",
        capacidades=Capacidades(
            efforts=["low", "medium", "high"],
            thinking=True,
            structured_outputs=True,
            contexto_max_tokens=200000,
        ),
        leido_en=T0,
    )


def perfil() -> PerfilConfig:
    return PerfilConfig(
        version=1,
        auditoria=AUDITORIA,
        org="acme",
        nombre=Perfil.estandar,
        roles={
            "critico-profundo": RequisitoRol(
                modelo={Proveedor.foundry: "sonnet-55-eastus2"}, effort="high", structured_outputs=True
            )
        },
        gate={
            Riesgo.bajo: TopeGate(criticos=1, iteraciones=1, adversarial=False),
            Riesgo.medio: TopeGate(criticos=1, iteraciones=1, adversarial=False),
            Riesgo.alto: TopeGate(criticos=2, iteraciones=2, adversarial=True),
        },
        exploradores={Riesgo.bajo: 1, Riesgo.medio: 1, Riesgo.alto: 2},
    )


def presupuesto() -> PresupuestoConfig:
    return PresupuestoConfig(
        version=1,
        auditoria=AUDITORIA,
        org="acme",
        por_unidad=Presupuesto(tokens_max=2_000_000, costo_usd_max=25.0),
    )


def proveedor_contexto() -> ProveedorContexto:
    return ProveedorContexto(
        version=1,
        auditoria=AUDITORIA,
        org="acme",
        rol="gobernanza",
        nombre="pce",
        url="https://pce.acme.internal/mcp",
        credencial_ref="secret://railspec-pce/api-key",
        politica_fallo="estricta",
        fases=[Fase.spec, Fase.plan],
    )


def telemetria() -> TelemetriaNodo:
    return TelemetriaNodo(
        id=uid(7),
        org="acme",
        workspace="certificados",
        repositorio="certificados-api",
        unidad="0001-emitir-pdf",
        nodo="critico-profundo",
        fase=GateFase.spec,
        tier=Riesgo.medio,
        proveedor=Proveedor.foundry,
        modelo="claude-sonnet-5-5",
        tokens_entrada=18000,
        tokens_salida=1200,
        tokens_cache_lectura=15000,
        costo_usd=0.08,
        duracion_ms=21000,
        veredicto=Veredicto.refinado,
        en=T0,
    )


def auditoria() -> RegistroAuditoria:
    return RegistroAuditoria(
        id=uid(8),
        alcance=ALCANCE_WS,
        evento=EventoAuditoria.llamada_modelo,
        actor=SERVIDOR,
        en=T0,
        repositorio="certificados-api",
        unidad="0001-emitir-pdf",
        nivel_codigo=NivelCodigo.restringido,
        proveedor=Proveedor.foundry,
        modelo="claude-sonnet-5-5",
        region="eastus2",
        sha256_enviado=sha("prompt"),
    )


EJEMPLOS = {
    "orden-de-trabajo": orden,
    "reporte-orden": reporte,
    "snapshot": snapshot,
    "evento-sync": evento,
    "estado-unidad": estado_unidad,
    "estado-local": estado_local,
    "insumo": insumo,
    "respuesta-chat": respuesta_chat,
    "veredicto-gate-salida": veredicto_permitido,
    "conversacion": conversacion,
    "mensaje-chat": mensaje_chat,
    "organizacion": organizacion,
    "workspace": workspace,
    "asignacion-rol": asignacion_rol,
    "vinculo-repositorio": vinculo,
    "modelo-catalogo": modelo_catalogo,
    "perfil": perfil,
    "presupuesto": presupuesto,
    "proveedor-contexto": proveedor_contexto,
    "telemetria-nodo": telemetria,
    "registro-auditoria": auditoria,
}


def serializar(nombre: str) -> str:
    datos = EJEMPLOS[nombre]().model_dump(mode="json", exclude_none=True)
    return json.dumps(datos, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[1] / "examples" / "v1"
    destino.mkdir(parents=True, exist_ok=True)
    for n in EJEMPLOS:
        (destino / f"{n}.json").write_text(serializar(n), encoding="utf-8")
        print(destino / f"{n}.json")
