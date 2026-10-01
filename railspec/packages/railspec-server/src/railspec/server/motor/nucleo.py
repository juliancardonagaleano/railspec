"""Servicios compartidos por los nodos del DAG y las tools.

Aquí vive toda escritura del estado remoto: lectura con versión, cambio y
escritura con bloqueo optimista, seguida del evento remoto→local que la
anuncia. También el registro de cada llamada a modelo (telemetría por nodo,
auditoría con hash de lo enviado, consumo contra el presupuesto).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from railspec.contracts.almacen import ConflictoVersion, GraphStore
from railspec.contracts.comun import (
    Actor,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    Fase,
    GateFase,
    NivelCodigo,
    Perfil,
    Riesgo,
    RolRepositorio,
)
from railspec.contracts.estado import Consumo, EjecucionModelo, EstadoUnidad
from railspec.contracts.eventos import (
    DIRECCION_POR_TIPO,
    CargaEvento,
    Direccion,
    EstadoActualizado,
    EventoSync,
)
from railspec.contracts.repositorio import EventoAuditoria, PerfilConfig, RegistroAuditoria, TelemetriaNodo

from ..proveedores.seleccion import Proveedores
from .gate import Llamada, LlamadaFallida, requisitos_del_gate
from .gobernanza import GobernanzaNoConfigurada, ProveedorGobernanza
from .perfiles import ACTOR_SERVIDOR, perfil_por_defecto, tope_gate

log = logging.getLogger("railspec.motor")

Reloj = Callable[[], datetime]


def reloj_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class Nucleo:
    almacen: Any  # AlmacenMongo u otro que cumpla StateStore y AlmacenMotor
    proveedores: Proveedores
    gobernanza: ProveedorGobernanza = field(default_factory=GobernanzaNoConfigurada)
    grafo: GraphStore | None = None
    reloj: Reloj = reloj_utc
    nuevo_id: Callable[[], uuid.UUID] = uuid.uuid4
    #: Oyentes de eventos (SSE de la consola, pruebas).
    oyentes: list[Callable[[EventoSync], None]] = field(default_factory=list)

    # --- estado -------------------------------------------------------------------

    def leer(self, alcance: AlcanceUnidad) -> EstadoUnidad:
        estado = self.almacen.obtener_estado(alcance)
        if estado is None:
            raise LookupError(f"unidad {alcance.unidad} no existe en {alcance.org}/{alcance.workspace}")
        return estado

    def escribir(
        self,
        alcance: AlcanceUnidad,
        cambio: Callable[[EstadoUnidad], dict[str, Any] | None],
        actor: Actor = ACTOR_SERVIDOR,
        *,
        reintentos: int = 3,
        anunciar: bool = True,
    ) -> EstadoUnidad:
        """Lee, aplica ``cambio`` (devuelve campos a actualizar o None) y escribe.

        Reintenta ante ``ConflictoVersion``: el cambio se recalcula sobre el
        estado nuevo, así que una resolución que ya no aplica lo decide ahí.
        """

        for intento in range(reintentos + 1):
            estado = self.leer(alcance)
            campos = cambio(estado)
            if campos is None:
                return estado
            nuevo = estado.model_copy(
                update={**campos, "actualizado_en": self.reloj(), "actualizado_por": actor}
            )
            try:
                guardado = self.almacen.guardar_estado(nuevo, estado.version)
            except ConflictoVersion:
                if intento == reintentos:
                    raise
                continue
            if anunciar:
                self.emitir(
                    alcance,
                    EstadoActualizado(version=guardado.version, fase=guardado.fase, estado=guardado.estado),
                    actor,
                )
            return guardado
        raise AssertionError("inalcanzable")

    # --- eventos --------------------------------------------------------------------

    def emitir(
        self, alcance: AlcanceUnidad, carga: CargaEvento, actor: Actor, causado_por: uuid.UUID | None = None
    ) -> EventoSync:
        direccion = DIRECCION_POR_TIPO[carga.tipo]
        for _ in range(5):
            secuencia = self.almacen.ultima_secuencia(alcance, direccion) + 1
            evento = EventoSync(
                id=self.nuevo_id(),
                direccion=direccion,
                unidad=alcance,
                secuencia=secuencia,
                emitido_en=self.reloj(),
                actor=actor,
                causado_por=causado_por,
                carga=carga,
            )
            try:
                self.almacen.registrar_evento(evento)
            except Exception as exc:  # colisión de secuencia con otra réplica
                if "duplicate" not in str(exc).lower():
                    raise
                continue
            self.notificar(evento)
            return evento
        raise RuntimeError("no se pudo asignar secuencia al evento")

    def notificar(self, evento: EventoSync) -> None:
        for oyente in self.oyentes:
            try:
                oyente(evento)
            except Exception:  # un oyente roto no frena el motor
                log.exception("oyente de eventos falló")

    # --- configuración ------------------------------------------------------------------

    def perfil(self, estado: EstadoUnidad) -> PerfilConfig:
        ws = AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace)
        return self.almacen.perfil(ws, estado.perfil) or perfil_por_defecto(ws, estado.perfil)

    def nivel(self, estado: EstadoUnidad, repositorio: str | None = None) -> NivelCodigo:
        repo = repositorio or primario(estado)
        vinculo = self.almacen.vinculo(
            AlcanceRepositorio(org=estado.unidad.org, workspace=estado.unidad.workspace, repositorio=repo)
        )
        # Sin vínculo registrado rige el valor por defecto de la política: restringido.
        return vinculo.nivel_codigo if vinculo else NivelCodigo.restringido

    async def validar_perfil(
        self, ws: AlcanceWorkspace, nombre: Perfil, repositorio: str, riesgo: Riesgo
    ) -> list[str]:
        """Motivos por los que el catálogo conectado no puede servir el perfil; vacío = válido.

        Se llama antes de crear la unidad: el motor rechaza el arranque en vez
        de degradar el modelo en silencio a mitad de un gate.
        """

        perfil = self.almacen.perfil(ws, nombre) or perfil_por_defecto(ws, nombre)
        vinculo = self.almacen.vinculo(
            AlcanceRepositorio(org=ws.org, workspace=ws.workspace, repositorio=repositorio)
        )
        nivel = vinculo.nivel_codigo if vinculo else NivelCodigo.restringido
        leer_ws = getattr(self.almacen, "workspace", None)
        workspace = leer_ws(ws) if leer_ws else None
        await self.proveedores.refrescar(ws.org)
        pares = requisitos_del_gate(perfil, nivel, tope_gate(perfil, riesgo).adversarial)
        return self.proveedores.validar(
            pares, nivel, org=ws.org, zona=workspace.zona_datos_azure if workspace else None
        )

    def zona(self, estado: EstadoUnidad) -> str | None:
        """Zona de datos de Azure del workspace (``Workspace.zona_datos_azure``), si la declara."""

        leer = getattr(self.almacen, "workspace", None)
        if leer is None:
            return None
        ws = leer(AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace))
        return ws.zona_datos_azure if ws else None

    # --- llamadas a modelo ------------------------------------------------------------------

    def registrar_llamadas(
        self,
        estado: EstadoUnidad,
        fase: Fase | GateFase,
        llamadas: list[Llamada],
        veredicto=None,
        *,
        fallidas: list[LlamadaFallida] = (),
    ) -> dict[str, Any]:
        """Telemetría y auditoría por llamada; devuelve los campos de estado a actualizar.

        Las fallidas también se auditan (lo enviado salió hacia el proveedor) y
        dejan telemetría con cero tokens; no tocan el consumo.
        """

        repo = primario(estado)
        nivel = self.nivel(estado, repo)
        ejecuciones = list(estado.modelo_ejecucion)
        consumo = estado.consumo
        alcance_ws = AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace)
        for f in fallidas:
            ahora = self.reloj()
            e = f.eleccion
            self.almacen.registrar_telemetria(
                TelemetriaNodo(
                    id=self.nuevo_id(),
                    org=estado.unidad.org,
                    workspace=estado.unidad.workspace,
                    repositorio=repo,
                    unidad=estado.unidad.unidad,
                    nodo=f.nodo[:120],
                    fase=fase,
                    tier=estado.riesgo,
                    proveedor=e.proveedor.proveedor,
                    modelo=e.modelo,
                    duracion_ms=f.duracion_ms,
                    en=ahora,
                )
            )
            self.almacen.registrar_auditoria(
                RegistroAuditoria(
                    id=self.nuevo_id(),
                    alcance=alcance_ws,
                    evento=EventoAuditoria.llamada_modelo,
                    actor=ACTOR_SERVIDOR,
                    en=ahora,
                    repositorio=repo,
                    unidad=estado.unidad.unidad,
                    nivel_codigo=nivel,
                    proveedor=e.proveedor.proveedor,
                    modelo=e.modelo,
                    region=e.region or e.proveedor.region or "desconocida",
                    sha256_enviado=f.sha256_enviado,
                    detalle={
                        "nodo": f.nodo[:120],
                        "rol": f.rol,
                        "resultado": "error",
                        "error": f.error[:300],
                        **({"despliegue": e.despliegue} if e.despliegue else {}),
                    },
                )
            )
        for ll in llamadas:
            r = ll.respuesta
            ahora = self.reloj()
            self.almacen.registrar_telemetria(
                TelemetriaNodo(
                    id=self.nuevo_id(),
                    org=estado.unidad.org,
                    workspace=estado.unidad.workspace,
                    repositorio=repo,
                    unidad=estado.unidad.unidad,
                    nodo=ll.nodo[:120],
                    fase=fase,
                    tier=estado.riesgo,
                    proveedor=r.proveedor,
                    modelo=r.modelo,
                    tokens_entrada=r.uso.tokens_entrada,
                    tokens_salida=r.uso.tokens_salida,
                    tokens_cache_lectura=r.uso.tokens_cache_lectura,
                    tokens_cache_escritura=r.uso.tokens_cache_escritura,
                    costo_usd=r.uso.costo_usd,
                    duracion_ms=r.uso.duracion_ms,
                    veredicto=veredicto,
                    en=ahora,
                )
            )
            self.almacen.registrar_auditoria(
                RegistroAuditoria(
                    id=self.nuevo_id(),
                    alcance=alcance_ws,
                    evento=EventoAuditoria.llamada_modelo,
                    actor=ACTOR_SERVIDOR,
                    en=ahora,
                    repositorio=repo,
                    unidad=estado.unidad.unidad,
                    nivel_codigo=nivel,
                    proveedor=r.proveedor,
                    modelo=r.modelo,
                    region=r.region or "desconocida",
                    sha256_enviado=ll.sha256_enviado,
                    detalle={
                        "nodo": ll.nodo[:120],
                        "rol": ll.rol,
                        "resultado": "ok",
                        "tokens_entrada": r.uso.tokens_entrada,
                        "tokens_salida": r.uso.tokens_salida,
                        "tokens_cache_lectura": r.uso.tokens_cache_lectura,
                        "tokens_cache_escritura": r.uso.tokens_cache_escritura,
                        **({"despliegue": ll.despliegue} if ll.despliegue else {}),
                    },
                )
            )
            ejecuciones.append(
                EjecucionModelo(
                    fase=fase,
                    rol=ll.rol,
                    nodo=ll.nodo[:120],
                    proveedor=r.proveedor,
                    modelo=r.modelo,
                    effort=ll.effort,
                    en=ahora,
                )
            )
            consumo = Consumo(
                tokens=consumo.tokens + r.uso.tokens,
                segundos=consumo.segundos + r.uso.duracion_ms // 1000,
                costo_usd=round(consumo.costo_usd + r.uso.costo_usd, 6),
            )
        return {"modelo_ejecucion": ejecuciones, "consumo": consumo}


def primario(estado: EstadoUnidad) -> str:
    return next(r.repositorio for r in estado.repositorios if r.rol == RolRepositorio.primario)


def presupuesto_agotado(estado: EstadoUnidad) -> str | None:
    p, c = estado.presupuesto, estado.consumo
    if p.tokens_max is not None and c.tokens >= p.tokens_max:
        return f"tokens {c.tokens}/{p.tokens_max}"
    if p.costo_usd_max is not None and c.costo_usd >= p.costo_usd_max:
        return f"costo {c.costo_usd:.2f}/{p.costo_usd_max:.2f} USD"
    if p.segundos_max is not None and c.segundos >= p.segundos_max:
        return f"segundos {c.segundos}/{p.segundos_max}"
    return None


__all__ = ["Direccion", "Nucleo", "Reloj", "presupuesto_agotado", "primario", "reloj_utc"]
