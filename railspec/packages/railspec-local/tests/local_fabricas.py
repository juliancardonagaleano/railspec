"""Servidor doble y utilidades para las pruebas del proxy local.

El doble implementa el contrato en memoria (orden vigente, rechazo de
reportes que no corresponden, versión de estado, checkpoints) y permite
simular caídas de red y respuestas perdidas. No reutiliza ``railspec-server``:
el proxy solo conoce el contrato.
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fabricas import JULIAN, SERVIDOR, insumo
from railspec.contracts.comun import (
    AlcanceUnidad,
    Criterio,
    EstadoFase,
    Fase,
    GobernanzaConsultada,
    Modo,
    NivelCodigo,
    Perfil,
    Riesgo,
    RolRepositorio,
)
from railspec.contracts.estado import (
    Checkpoint,
    ConversionModo,
    EstadoUnidad,
    RepositorioUnidad,
    TipoCheckpoint,
)
from railspec.contracts.orden import (
    AlcanceArchivos,
    Artefacto,
    ContextoArmado,
    OrdenImplementar,
    OrdenRedactar,
    ReporteRequerido,
    Tarea,
)
from railspec.contracts.reporte import ReporteOrden
from railspec.contracts.snapshot import DeltaIndice, Embedding, MotorIndice, Simbolo, TipoSimbolo, id_simbolo
from railspec.contracts.tools import (
    AvanceCerrada,
    AvanceCheckpoint,
    AvanceOrden,
    CodigoError,
    ErrorTool,
    EstadoSalida,
    GraphQueryEntrada,
    GraphQuerySalida,
    InsumoGetSalida,
    UnitAdvanceEntrada,
    UnitAdvanceSalida,
    UnitApproveEntrada,
    UnitReportSalida,
    UnitSetModeEntrada,
    UnitStartEntrada,
    UnitStartSalida,
    UnitStatusEntrada,
    UnitStatusSalida,
)
from railspec.local import config
from railspec.local.cliente import ClienteServidor
from railspec.local.errores import ErrorServidor, SinConexion
from railspec.local.proxy import ProxyLocal

T0 = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)
FabricaOrden = Callable[[EstadoUnidad, int, UUID], Any]


def uid(n: int) -> UUID:
    return UUID(f"00000000-0000-4000-9000-{n:012d}")


def orden_implementar(
    estado: EstadoUnidad, secuencia: int, orden_id: UUID, **cambios: Any
) -> OrdenImplementar:
    datos: dict[str, Any] = dict(
        id=orden_id,
        secuencia=secuencia,
        unidad=estado.unidad,
        repositorio=estado.repositorios[0].repositorio,
        base_commit=estado.repositorios[0].base_commit,
        fase=Fase.implement,
        emitida_en=T0,
        instrucciones="Implementa G1.",
        contexto=ContextoArmado(gobernanza_consultada=GobernanzaConsultada.si),
        alcance=AlcanceArchivos(permitidos=["src/**"], prohibidos=["src/generado/**"]),
        criterios=[Criterio(id="CA-01", texto="Suma correcta.")],
        comando_validacion="python -c \"print('def secreto(): pass'); print('2 passed')\"",
        reporte_requerido=ReporteRequerido(snapshot=True, validacion=True),
        grupo="G1",
        tareas=[Tarea(id="T-01", descripcion="Corregir suma.", criterios=["CA-01"])],
    )
    datos.update(cambios)
    return OrdenImplementar(**datos)


def orden_redactar(estado: EstadoUnidad, secuencia: int, orden_id: UUID) -> OrdenRedactar:
    return OrdenRedactar(
        id=orden_id,
        secuencia=secuencia,
        unidad=estado.unidad,
        repositorio=estado.repositorios[0].repositorio,
        base_commit=estado.repositorios[0].base_commit,
        fase=Fase.spec,
        emitida_en=T0,
        instrucciones="Redacta el spec.",
        contexto=ContextoArmado(gobernanza_consultada=GobernanzaConsultada.si),
        artefacto=Artefacto.spec,
        ruta_artefacto="specs/0001/spec.md",
        plantilla="# Spec\n",
    )


class ServidorDoble:
    """Transporte en memoria que se comporta como railspec-server según el contrato."""

    def __init__(self, ordenes: list[FabricaOrden] | None = None) -> None:
        self.ordenes: list[FabricaOrden] = list(ordenes or [])
        self.estado: EstadoUnidad | None = None
        self.orden: Any = None
        self.aceptadas: set[UUID] = set()
        self.reportes: list[ReporteOrden] = []
        self.llamadas: list[tuple[str, dict[str, Any]]] = []
        self.conectado = True
        self.perder_respuesta = False
        self.rechazar_con: CodigoError | None = None
        self.checkpoint: Checkpoint | None = None
        self.resoluciones: list[UnitApproveEntrada] = []
        self.consultas_grafo: list[GraphQueryEntrada] = []
        self._n = 100

    def _id(self) -> UUID:
        self._n += 1
        return uid(self._n)

    def _actualizar(self, **cambios: Any) -> None:
        assert self.estado is not None
        self.estado = EstadoUnidad.model_validate(
            {
                **self.estado.model_dump(),
                **cambios,
                "version": self.estado.version + 1,
                "actualizado_en": self.estado.actualizado_en + timedelta(seconds=1),
            }
        )

    def _error(self, codigo: CodigoError, detalle: str) -> ErrorServidor:
        return ErrorServidor(ErrorTool(codigo=codigo, detalle=detalle))

    async def llamar(self, tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        self.llamadas.append((tool, argumentos))
        if not self.conectado:
            raise SinConexion("servidor doble desconectado")
        salida = getattr(self, "_" + tool.replace(".", "_"))(argumentos)
        return salida.model_dump(mode="json")

    def _unit_start(self, args: dict[str, Any]) -> UnitStartSalida:
        entrada = UnitStartEntrada.model_validate(args)
        repo = entrada.repositorios[0]
        self.estado = EstadoUnidad(
            unidad=AlcanceUnidad(
                org=entrada.alcance.org, workspace=entrada.alcance.workspace, unidad="0001-sumar"
            ),
            version=1,
            titulo=entrada.titulo,
            dueno=JULIAN,
            arnes=entrada.arnes,
            repositorios=[
                RepositorioUnidad(
                    repositorio=repo.repositorio,
                    rol=RolRepositorio.primario,
                    rama=repo.rama,
                    base_commit=repo.base_commit,
                )
            ],
            fase=Fase.spec,
            estado=EstadoFase.en_progreso,
            modo=Modo.interactivo,
            riesgo=Riesgo.medio,
            perfil=Perfil.estandar,
            insumos=entrada.insumos,
            pedido=entrada.pedido,
            creado_en=T0,
            actualizado_en=T0,
            actualizado_por=SERVIDOR,
        )
        return UnitStartSalida(estado=self.estado, version_contrato_negociada="1.2")

    def _unit_advance(self, args: dict[str, Any]) -> UnitAdvanceSalida:
        UnitAdvanceEntrada.model_validate(args)
        assert self.estado is not None
        if self.checkpoint is not None:
            return UnitAdvanceSalida(
                version_estado=self.estado.version, avance=AvanceCheckpoint(checkpoint=self.checkpoint)
            )
        if self.orden is None and self.ordenes:
            fabrica = self.ordenes.pop(0)
            secuencia = self.estado.secuencia_ordenes + 1
            self.orden = fabrica(self.estado, secuencia, self._id())
            self._actualizar(orden_vigente=self.orden.id, secuencia_ordenes=secuencia, fase=self.orden.fase)
        if self.orden is None:
            return UnitAdvanceSalida(version_estado=self.estado.version, avance=AvanceCerrada())
        return UnitAdvanceSalida(version_estado=self.estado.version, avance=AvanceOrden(orden=self.orden))

    def _unit_report(self, args: dict[str, Any]) -> UnitReportSalida:
        reporte = ReporteOrden.model_validate(args)
        assert self.estado is not None
        if self.rechazar_con is not None:
            codigo, self.rechazar_con = self.rechazar_con, None
            self.orden = None
            self._actualizar(orden_vigente=None)
            raise self._error(codigo, "rechazado por el doble")
        if reporte.orden_id in self.aceptadas:
            raise self._error(CodigoError.secuencia_duplicada, "reporte ya aceptado")
        if self.orden is None or reporte.orden_id != self.orden.id:
            raise self._error(CodigoError.orden_no_vigente, "no es la orden vigente")
        if reporte.base_commit != self.orden.base_commit:
            raise self._error(CodigoError.base_commit_distinto, "otro base_commit")
        self.reportes.append(reporte)
        self.aceptadas.add(reporte.orden_id)
        self.orden = None
        self._actualizar(orden_vigente=None)
        if self.perder_respuesta:
            self.perder_respuesta = False
            raise SinConexion("respuesta perdida")
        return UnitReportSalida(version_estado=self.estado.version)

    def _unit_status(self, args: dict[str, Any]) -> UnitStatusSalida:
        UnitStatusEntrada.model_validate(args)
        assert self.estado is not None
        return UnitStatusSalida(estado=self.estado, orden_vigente=self.orden)

    def _unit_approve(self, args: dict[str, Any]) -> EstadoSalida:
        entrada = UnitApproveEntrada.model_validate(args)
        if self.checkpoint is None or entrada.checkpoint != self.checkpoint.id:
            raise self._error(CodigoError.checkpoint_ya_resuelto, "ya resuelto")
        self.resoluciones.append(entrada)
        self.checkpoint = None
        self._actualizar(checkpoint_pendiente=None)
        assert self.estado is not None
        return EstadoSalida(estado=self.estado)

    def _unit_set_mode(self, args: dict[str, Any]) -> EstadoSalida:
        entrada = UnitSetModeEntrada.model_validate(args)
        assert self.estado is not None
        if self.estado.fase not in (Fase.research, Fase.spec):
            raise self._error(CodigoError.conversion_no_permitida, "solo tras research o el spec")
        conversion = ConversionModo(
            de=self.estado.modo,
            a=entrada.modo,
            actor=JULIAN,
            en=T0,
            motivo=entrada.motivo,
            tras=self.estado.fase,
        )
        self._actualizar(modo=entrada.modo, modo_conversion=[*self.estado.modo_conversion, conversion])
        return EstadoSalida(estado=self.estado)

    def _insumo_get(self, args: dict[str, Any]) -> InsumoGetSalida:
        return InsumoGetSalida(insumo=insumo())

    def _graph_query(self, args: dict[str, Any]) -> GraphQuerySalida:
        self.consultas_grafo.append(GraphQueryEntrada.model_validate(args))
        return GraphQuerySalida(resultados=[], commits={})

    def abrir_checkpoint(self) -> Checkpoint:
        self.checkpoint = Checkpoint(
            id=self._id(),
            tipo=TipoCheckpoint.aprobar_spec,
            fase=Fase.spec,
            pregunta="¿Apruebas el spec?",
            abierto_en=T0,
        )
        self._actualizar(checkpoint_pendiente=self.checkpoint)
        return self.checkpoint


class IndexadorDoble:
    """Indexador determinista: un símbolo por archivo .py tocado (líneas 1-2)."""

    def __init__(self) -> None:
        self.consultas: list[str] = []

    def delta(self, worktree: Path, repositorio: str, base_commit: str, rutas: list[str], excluir: list[str]):
        simbolos = []
        for ruta in rutas:
            archivo = worktree / ruta
            if not ruta.endswith(".py") or not archivo.is_file():
                continue
            texto = b"".join(archivo.read_bytes().splitlines(keepends=True)[:2])
            simbolos.append(
                Simbolo(
                    id=id_simbolo(repositorio, ruta, "funcion", ruta),
                    nombre=ruta,
                    tipo=TipoSimbolo.funcion,
                    ruta=ruta,
                    linea_inicio=1,
                    linea_fin=2,
                    sha256=hashlib.sha256(texto).hexdigest(),
                )
            )
        return DeltaIndice(
            motor=MotorIndice(version="0.11.0"),
            simbolos_upsert=simbolos,
            embeddings=[
                Embedding(simbolo=s.id, vector_b64=base64.b64encode(bytes(768)).decode()) for s in simbolos
            ],
        )

    def embedding_consulta(self, texto: str) -> bytes | None:
        self.consultas.append(texto)
        return bytes(range(256)) * 3


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def repo_git(raiz: Path) -> Path:
    raiz.mkdir(parents=True, exist_ok=True)
    sh(raiz, "init", "-q", "-b", "main")
    sh(raiz, "config", "user.email", "dev@example.com")
    sh(raiz, "config", "user.name", "Dev")
    (raiz / "src").mkdir()
    (raiz / "src" / "calc.py").write_text("def suma(a, b):\n    return a - b\n", encoding="utf-8")
    (raiz / "README.md").write_text("demo\n", encoding="utf-8")
    sh(raiz, "add", "-A")
    sh(raiz, "commit", "-q", "-m", "base")
    return raiz


def crear_proxy(
    tmp: Path,
    servidor: ServidorDoble,
    nivel: NivelCodigo = NivelCodigo.restringido,
    indexador: Any = None,
) -> ProxyLocal:
    raiz = repo_git(tmp / "certificados-api")
    config.escribir_config_repositorio(
        raiz,
        config.ConfigRepositorio(
            org="acme", workspace="certificados", repositorio="certificados-api", nivel_codigo=nivel
        ),
    )
    sh(raiz, "add", "-A")
    sh(raiz, "commit", "-q", "-m", "railspec")
    cfg = config.cargar(raiz, entorno={})
    return ProxyLocal(
        cfg, ClienteServidor(servidor), indexador=indexador, reloj=lambda: T0 + timedelta(hours=1)
    )
