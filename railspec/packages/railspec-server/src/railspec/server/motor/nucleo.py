"""Servicios compartidos por los nodos del DAG y las tools.

Aquí vive toda escritura del estado remoto: lectura con versión, cambio y
escritura con bloqueo optimista, seguida del evento remoto→local que la
anuncia. También el registro de cada llamada a modelo (telemetría por nodo,
auditoría con hash de lo enviado, consumo contra el presupuesto).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from railspec.contracts.almacen import ConflictoVersion, GraphStore
from railspec.contracts.comun import (
    MODOS_CON_MANDATO,
    Actor,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    CausaEscalado,
    Fase,
    GateFase,
    NivelCodigo,
    Perfil,
    Riesgo,
    RolRepositorio,
    Veredicto,
)
from railspec.contracts.estado import EjecucionModelo, EstadoUnidad
from railspec.contracts.eventos import (
    DIRECCION_POR_TIPO,
    CargaEvento,
    Direccion,
    EstadoActualizado,
    EventoSync,
)
from railspec.contracts.repositorio import EventoAuditoria, PerfilConfig, RegistroAuditoria, TelemetriaNodo

from ..proveedores.base import Uso
from ..proveedores.seleccion import Proveedores
from . import presupuesto
from .gate import Llamada, LlamadaFallida, requisitos_del_gate
from .gobernanza import GobernanzaNoConfigurada, ProveedorGobernanza
from .perfiles import ACTOR_SERVIDOR, perfil_por_defecto, tope_gate

if TYPE_CHECKING:
    from .mandatos import Mandatos

log = logging.getLogger("railspec.motor")

Reloj = Callable[[], datetime]

#: De más a menos restrictivo: con varios repositorios en una unidad rige el que esté más a la izquierda.
RESTRICCION = (NivelCodigo.restringido, NivelCodigo.interno, NivelCodigo.abierto)


def mas_restrictivo(niveles: Iterable[NivelCodigo]) -> NivelCodigo:
    """El nivel más restrictivo de ``niveles``; sin ninguno, ``restringido`` (el por defecto)."""

    return min(niveles, key=RESTRICCION.index, default=NivelCodigo.restringido)


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
    #: Insumos del chat (``chat.resolucion.ResolutorInsumos``); sin él, ``unit.start`` no acepta insumos.
    insumos: Any | None = None
    _mandatos: Any = field(default=None, init=False, repr=False, compare=False)

    @property
    def mandatos(self) -> Mandatos:
        """Servicio del mandato (1.11); nace con el primer uso porque ``mandatos`` importa al motor."""

        if self._mandatos is None:
            from .mandatos import Mandatos

            self._mandatos = Mandatos(self)
        return self._mandatos

    #: ``avisos.servicio.ServicioAvisos``: avisa por Teams o correo cuando un gate escala. Sin él, nada.
    avisos: Any | None = None

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

    def perfil_inicial(self, ws: AlcanceWorkspace, pedido: Perfil | None) -> Perfil:
        """Perfil con el que nace una unidad: el pedido, si no el del workspace, si no ``estandar``."""

        if pedido is not None:
            return pedido
        leer_ws = getattr(self.almacen, "workspace", None)
        workspace = leer_ws(ws) if leer_ws else None
        return workspace.perfil_por_defecto if workspace else Perfil.estandar

    def nivel_de(self, ws: AlcanceWorkspace, repositorio: str) -> NivelCodigo:
        """Nivel de un repositorio según su vínculo; sin vínculo rige ``restringido``."""

        vinculo = self.almacen.vinculo(
            AlcanceRepositorio(org=ws.org, workspace=ws.workspace, repositorio=repositorio)
        )
        return vinculo.nivel_codigo if vinculo else NivelCodigo.restringido

    def niveles(self, ws: AlcanceWorkspace, repositorios: Iterable[str]) -> NivelCodigo:
        """Nivel efectivo de un conjunto de repositorios: gana el más restrictivo."""

        return mas_restrictivo(self.nivel_de(ws, r) for r in repositorios)

    def nivel(self, estado: EstadoUnidad, repositorio: str | None = None) -> NivelCodigo:
        """Nivel de ``repositorio`` o, sin él, el efectivo de la unidad.

        El nivel solo gobierna qué material de código viaja al modelo (fragmentos en ``interno``,
        diff en ``abierto``) y se audita; no elige proveedor, modelo ni región. Todo lo que sale de
        la unidad hacia un modelo (material del gate, auditoría) usa el efectivo: el más restrictivo
        de todos sus repositorios, no el del primario. Quien necesita el de un repositorio concreto
        (el nivel que declara su snapshot) lo pide por nombre.

        Rige el nivel congelado al crear la unidad (``RepositorioUnidad.nivel_codigo`` y
        ``EstadoUnidad.nivel_efectivo``, contrato 1.7): un cambio posterior del vínculo en la consola no
        altera las unidades en curso. Una unidad anterior a 1.7 (sin nivel congelado) lee el vínculo vivo.
        """

        if repositorio is not None:
            congelado = next(
                (r.nivel_codigo for r in estado.repositorios if r.repositorio == repositorio), None
            )
            if congelado is not None:
                return congelado
            ws = AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace)
            return self.nivel_de(ws, repositorio)
        if estado.nivel_efectivo is not None:
            return estado.nivel_efectivo
        ws = AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace)
        return self.niveles(ws, (r.repositorio for r in estado.repositorios))

    def congelar_niveles(
        self, ws: AlcanceWorkspace, repositorios: Iterable[str]
    ) -> tuple[list[NivelCodigo], NivelCodigo]:
        """Niveles de los vínculos de ``repositorios`` (en orden) y el efectivo, para congelarlos."""

        niveles = [self.nivel_de(ws, r) for r in repositorios]
        return niveles, mas_restrictivo(niveles)

    async def validar_perfil(
        self, ws: AlcanceWorkspace, nombre: Perfil, repositorios: str | Iterable[str], riesgo: Riesgo
    ) -> list[str]:
        """Motivos por los que el catálogo conectado no puede servir el perfil; vacío = válido.

        Se llama antes de crear la unidad: el motor rechaza el arranque en vez
        de degradar el modelo en silencio a mitad de un gate. El nivel de los
        repositorios solo elige las claves por nivel del perfil (el más restrictivo
        de todos, el mismo con el que correrá el gate); no restringe proveedores.
        """

        perfil = self.almacen.perfil(ws, nombre) or perfil_por_defecto(ws, nombre)
        nivel = self.niveles(ws, [repositorios] if isinstance(repositorios, str) else repositorios)
        await self.proveedores.refrescar(ws.org)
        pares = requisitos_del_gate(perfil, nivel, tope_gate(perfil, riesgo).adversarial)
        return self.proveedores.validar(pares, org=ws.org, suscripcion=perfil.suscripcion)

    # --- llamadas a modelo ------------------------------------------------------------------

    def registrar_llamadas(
        self,
        estado: EstadoUnidad,
        fase: Fase | GateFase,
        llamadas: list[Llamada],
        veredicto: Veredicto | None = None,
        *,
        fallidas: list[LlamadaFallida] = (),
    ) -> None:
        """Telemetría y auditoría por llamada, una sola vez.

        No va dentro de ``escribir``: el reintento del bloqueo optimista vuelve a
        correr el cambio y duplicaría las filas. El consumo se aplica aparte
        (``contabilizar``). Las fallidas también se auditan (lo enviado salió hacia
        el proveedor) y dejan telemetría con cero tokens. ``veredicto`` es el de la
        iteración del gate a la que pertenecen las llamadas.
        """

        repo = primario(estado)
        nivel = self.nivel(estado)  # efectivo: el más restrictivo de los repositorios de la unidad
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
                        **({"suscripcion": e.suscripcion} if e.suscripcion else {}),
                    },
                )
            )
        for ll in llamadas:
            r = ll.respuesta
            # Un acierto de caché no salió hacia el proveedor: telemetría a cero y sin auditoría.
            u = Uso() if ll.desde_cache else r.uso
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
                    tokens_entrada=u.tokens_entrada,
                    tokens_salida=u.tokens_salida,
                    tokens_cache_lectura=u.tokens_cache_lectura,
                    tokens_cache_escritura=u.tokens_cache_escritura,
                    costo_usd=u.costo_usd,
                    duracion_ms=u.duracion_ms,
                    veredicto=veredicto,
                    en=ahora,
                )
            )
            if not ll.desde_cache:
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
                            "tokens_entrada": u.tokens_entrada,
                            "tokens_salida": u.tokens_salida,
                            "tokens_cache_lectura": u.tokens_cache_lectura,
                            "tokens_cache_escritura": u.tokens_cache_escritura,
                            **({"despliegue": ll.despliegue} if ll.despliegue else {}),
                            **({"suscripcion": ll.suscripcion} if ll.suscripcion else {}),
                        },
                    )
                )

    def contabilizar(
        self, estado: EstadoUnidad, fase: Fase | GateFase, llamadas: list[Llamada]
    ) -> dict[str, Any]:
        """Campos del estado (consumo y ejecuciones) tras ``llamadas``; sin efectos fuera de ``estado``.

        Es el ``cambio`` de ``escribir``: se recalcula sobre el estado vigente en
        cada reintento. Un acierto de caché no consume; las fallidas tampoco.
        """

        ejecuciones = list(estado.modelo_ejecucion)
        consumo = estado.consumo
        for ll in llamadas:
            r = ll.respuesta
            ejecuciones.append(
                EjecucionModelo(
                    fase=fase,
                    rol=ll.rol,
                    nodo=ll.nodo[:120],
                    proveedor=r.proveedor,
                    modelo=r.modelo,
                    effort=ll.effort,
                    en=self.reloj(),
                )
            )
            if not ll.desde_cache:
                consumo = presupuesto.con_uso(consumo, r.uso + Uso(llamadas=1))
        return {"modelo_ejecucion": ejecuciones, "consumo": consumo}

    def presupuesto_agotado(
        self, estado: EstadoUnidad, gate: GateFase, fase: Fase, gastado: Uso = presupuesto.SIN_GASTO
    ) -> str | None:
        """Qué tope del presupuesto (unidad, fase, mes o mandato) impide otra llamada de ``gate``, o ``None``.

        El del mandato (``por_mandato``, 1.11) solo rige para una unidad supervisada o desatendida.
        """

        motivo = presupuesto.agotado(self.almacen, self.reloj(), estado, gate, fase, gastado)
        if motivo is None and estado.modo in MODOS_CON_MANDATO:
            motivo = self.mandatos.presupuesto_agotado(estado, gastado)
        return motivo

    def avisar_escalado(self, alcance: AlcanceUnidad, fase: GateFase, causa: str, motivo: str) -> None:
        """Avisa (Teams o correo) que un gate escaló. Nunca lanza ni espera a la red.

        Solo salen identificadores, la causa y, si es el presupuesto, el tope y el consumo en números: el
        ``motivo`` completo puede traer texto de hallazgos y no se manda.
        """

        if self.avisos is None:
            return
        try:
            from ..avisos.modelo import EventoAviso, TipoAviso
            from ..avisos.servicio import tope_de_motivo

            presupuesto_agotado = causa == CausaEscalado.presupuesto_agotado.value
            tope, consumo = tope_de_motivo(motivo) if presupuesto_agotado else (None, None)
            self.avisos.notificar(
                EventoAviso(
                    tipo=TipoAviso.presupuesto_agotado if presupuesto_agotado else TipoAviso.gate_escalado,
                    org=alcance.org,
                    workspace=alcance.workspace,
                    unidad=alcance.unidad,
                    fase=fase.value,
                    causa=causa,
                    tope=tope,
                    consumo=consumo,
                    en=self.reloj(),
                )
            )
        except Exception:  # un aviso nunca frena un gate
            log.exception("no se pudo avisar del escalado de %s", alcance.unidad)


def primario(estado: EstadoUnidad) -> str:
    return next(r.repositorio for r in estado.repositorios if r.rol == RolRepositorio.primario)


__all__ = ["Direccion", "Nucleo", "RESTRICCION", "Reloj", "mas_restrictivo", "primario", "reloj_utc"]
