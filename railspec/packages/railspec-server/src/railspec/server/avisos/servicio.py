"""Servicio de avisos: configuración por organización, envío sin bloquear al motor e informe semanal.

- ``notificar`` lo llama el motor al escalar un gate. Registra el escalado (lo cuenta el informe) y lo
  envía desde un hilo aparte: **nunca lanza ni espera a la red**, así que un Teams caído no frena un gate.
- Cada envío se reserva por ``(clave, canal)`` en el estado, así que varias réplicas, un reintento del
  motor o un gate que se repite en la misma hora no mandan el mismo aviso dos veces. Un envío fallido
  libera su reserva (el informe se reintenta en la siguiente pasada del fondo; un aviso, en el
  siguiente escalado).
- Un tope por organización y hora corta cualquier tormenta (p. ej. el presupuesto mensual agotado hace
  escalar a todas las unidades a la vez); lo omitido queda en el registro de escalados y en el informe.
- El URL del webhook de Teams es secreto: se cifra con la clave maestra (``RAILSPEC_CLAVE_MAESTRA``,
  dominio propio) y no sale por ninguna respuesta, log ni auditoría.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from railspec.contracts.repositorio import Auditoria

from ..contexto.destinos import DestinoNoPermitido
from ..proveedores.cifrado import VARIABLE, Cifrador, ErrorCifrado
from .canales import CanalCorreo, CanalTeams, ConfigSmtp, Mensaje, ResultadoEnvio
from .informe import construir_informe
from .mensajes import asunto, asunto_informe, mensaje_evento, mensaje_informe, mensaje_prueba
from .modelo import (
    MAX_DESTINATARIOS,
    Canal,
    ConfigAvisos,
    EventoAviso,
    EventosAviso,
    InformeConfig,
    TipoAviso,
    correo_valido,
)

log = logging.getLogger("railspec.avisos")

#: Avisos por organización y hora (cuenta las reservas de envío; el informe y las pruebas no cuentan).
MAX_AVISOS_HORA = 30
#: Cada cuánto mira el fondo si toca un informe.
BARRIDO_INFORME_S = 1800.0
#: Si el servidor estuvo caído a la hora del informe, lo manda al volver dentro de estas horas.
VENTANA_INFORME_H = 12
SECRETO_TEAMS = "teams"
_DOMINIO_CIFRADO = "aviso"
_TOPE = re.compile(
    r"\((?P<tope>por_unidad|por_fase [a-z]+|mensual_usd \d{4}-\d{2}): (?P<consumo>[^()]{1,80})\)"
)
_CONSUMO = re.compile(r"(?:tokens \d+/\d+|costo \d+(?:\.\d+)?/\d+(?:\.\d+)? USD|segundos \d+/\d+)")


class ErrorAvisos(Exception):
    def __init__(self, codigo: str, detalle: str, estado: int = 400) -> None:
        super().__init__(detalle)
        self.codigo, self.detalle, self.estado = codigo, detalle, estado


@dataclass
class EntradaAvisos:
    activo: bool
    #: ``None`` = conservar el que hay.
    teams_url: str | None
    quitar_teams: bool
    destinatarios: list[str]
    eventos: EventosAviso
    informe: InformeConfig


def tope_de_motivo(motivo: str) -> tuple[str | None, str | None]:
    """``(tope, consumo)`` del motivo de un escalado por presupuesto, solo con la forma numérica esperada."""

    m = _TOPE.search(motivo or "")
    if m is None or _CONSUMO.fullmatch(m.group("consumo")) is None:
        return None, None
    return m.group("tope"), m.group("consumo")


def _utc(f: datetime) -> datetime:
    return f.replace(tzinfo=UTC) if f.tzinfo is None else f.astimezone(UTC)


def instante_programado(ahora: datetime, dia_semana: int, hora_utc: int) -> datetime:
    """La última vez, hasta ``ahora``, que fue ``dia_semana`` a las ``hora_utc`` en punto (UTC)."""

    ahora = _utc(ahora)
    base = ahora.replace(hour=hora_utc, minute=0, second=0, microsecond=0)
    atras = (ahora.weekday() - dia_semana) % 7
    candidato = base - timedelta(days=atras)
    return candidato if candidato <= ahora else candidato - timedelta(days=7)


class ServicioAvisos:
    def __init__(
        self,
        datos: Any,
        almacen: Any,
        cifrador: Cifrador | None,
        smtp: ConfigSmtp | None = None,
        *,
        url_consola: str | None = None,
        teams: CanalTeams | None = None,
        correo: CanalCorreo | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        ejecutor: Any | None = None,
    ) -> None:
        self._datos = datos
        self._almacen = almacen
        self._cifrador = cifrador
        self._base = url_consola
        self._teams = teams or CanalTeams()
        self._correo = correo if correo is not None else (CanalCorreo(smtp) if smtp else None)
        self._reloj = reloj
        self._ejecutor = ejecutor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="railspec-avisos")

    # --- disponibilidad y lectura --------------------------------------------------------------

    @property
    def cifrado_disponible(self) -> bool:
        return self._cifrador is not None

    def canales(self) -> dict[str, dict[str, Any]]:
        return {
            "teams": {"disponible": self.cifrado_disponible},
            "correo": {
                "disponible": self._correo is not None,
                "motivo": None if self._correo else "el servidor no tiene RAILSPEC_SMTP_URL",
            },
        }

    def vista(self, org: str) -> dict[str, Any]:
        from .canales import HOSTS_TEAMS

        c = self._datos.avisos_config(org)
        return {
            "cifrado": {"disponible": self.cifrado_disponible, "variable": VARIABLE},
            "canales": self.canales(),
            "hosts_teams": sorted(HOSTS_TEAMS),
            "config": self.vista_config(c),
            "ultimos": [self._fila_historial(d) for d in self._datos.historial_avisos(org, 20)],
        }

    @staticmethod
    def vista_config(c: ConfigAvisos | None) -> dict[str, Any]:
        c = c or ConfigAvisos.model_construct(
            version=None,
            activo=False,
            teams_host=None,
            destinatarios=[],
            eventos=EventosAviso(),
            informe=InformeConfig(),
        )
        return {
            "version": c.version,
            "activo": c.activo,
            "teams": {"configurado": c.teams_host is not None, "host": c.teams_host},
            "correo": {"destinatarios": list(c.destinatarios)},
            "eventos": c.eventos.model_dump(),
            "informe": c.informe.model_dump(),
        }

    @staticmethod
    def _fila_historial(d: dict[str, Any]) -> dict[str, Any]:
        return {
            "en": _utc(d["en"]).isoformat(),
            "tipo": d.get("tipo"),
            "canal": d.get("canal"),
            "resultado": "enviado" if d.get("ok") else "error",
            "codigo": d.get("codigo"),
            "workspace": d.get("workspace"),
            "unidad": d.get("unidad"),
        }

    # --- escritura ------------------------------------------------------------------------------

    def guardar(
        self, org: str, entrada: EntradaAvisos, version: int | None, auditoria: Auditoria
    ) -> ConfigAvisos:
        previa = self._datos.avisos_config(org)
        if version is not None and previa is None:
            raise ErrorAvisos("no-encontrada", "todavía no hay configuración de avisos", 404)
        if entrada.quitar_teams and entrada.teams_url:
            raise ErrorAvisos("campos-invalidos", "no se puede quitar y cambiar el webhook a la vez")
        try:
            destinatarios = [correo_valido(x) for x in entrada.destinatarios]
        except ValueError as exc:
            raise ErrorAvisos("destinatario-invalido", str(exc)) from None
        if len(destinatarios) > MAX_DESTINATARIOS or len(set(destinatarios)) != len(destinatarios):
            raise ErrorAvisos(
                "destinatario-invalido", f"hasta {MAX_DESTINATARIOS} destinatarios, sin repetir"
            )
        host = previa.teams_host if previa else None
        cifrado = None
        url = (entrada.teams_url or "").strip() or None
        if url is not None:
            if self._cifrador is None:
                raise ErrorAvisos(
                    "clave-maestra-ausente",
                    f"el servidor no tiene {VARIABLE}: sin ella no puede guardar el webhook de Teams",
                    409,
                )
            try:
                host = self._teams.validar(url)
            except DestinoNoPermitido as exc:
                raise ErrorAvisos("destino-no-permitido", f"webhook de Teams no admitido: {exc}") from None
            cifrado = self._cifrador.cifrar(url, org, SECRETO_TEAMS, _DOMINIO_CIFRADO)
        elif entrada.quitar_teams:
            host = None
        nueva = ConfigAvisos(
            org=org,
            version=(version or 0) + 1,
            activo=entrada.activo,
            teams_host=host,
            destinatarios=destinatarios,
            eventos=entrada.eventos,
            informe=entrada.informe,
            auditoria=auditoria,
        )
        # Primero el documento: un ``ConflictoVersion`` no toca el secreto guardado.
        self._datos.guardar_avisos_config(nueva, version)
        if cifrado is not None:
            self._datos.guardar_secreto_aviso(org, SECRETO_TEAMS, cifrado)
        elif entrada.quitar_teams:
            self._datos.borrar_secreto_aviso(org, SECRETO_TEAMS)
        return nueva

    # --- envío ----------------------------------------------------------------------------------

    def _url_teams(self, org: str) -> str | None:
        cifrado = self._datos.secreto_aviso(org, SECRETO_TEAMS)
        if cifrado is None:
            return None
        if self._cifrador is None:
            raise ErrorCifrado("clave-maestra-ausente", "sin clave maestra")
        return self._cifrador.descifrar(cifrado, org, SECRETO_TEAMS, _DOMINIO_CIFRADO)

    def _por_canal(
        self, org: str, config: ConfigAvisos | None, mensaje: Mensaje, asunto_correo: str
    ) -> list[tuple[str, Callable[[], ResultadoEnvio]]]:
        """Los envíos que corresponden a la configuración, sin ejecutarlos."""

        envios: list[tuple[str, Callable[[], ResultadoEnvio]]] = []
        if config is None:
            return envios
        if config.teams_host is not None:

            def a_teams() -> ResultadoEnvio:
                try:
                    url = self._url_teams(org)
                except ErrorCifrado as exc:
                    return ResultadoEnvio(Canal.teams.value, False, exc.codigo)
                if url is None:
                    return ResultadoEnvio(Canal.teams.value, False, "sin-webhook")
                return self._teams.enviar(url, mensaje)

            envios.append((Canal.teams.value, a_teams))
        if config.destinatarios and self._correo is not None:
            correo = self._correo
            envios.append(
                (
                    Canal.correo.value,
                    lambda: correo.enviar(list(config.destinatarios), asunto_correo, mensaje),
                )
            )
        return envios

    def _despachar(
        self,
        org: str,
        clave: str | None,
        tipo: TipoAviso,
        envios: list[tuple[str, Callable[[], ResultadoEnvio]]],
        extra: dict[str, Any],
    ) -> list[ResultadoEnvio]:
        """Ejecuta los envíos; con ``clave`` reserva cada canal antes y libera la reserva si falla."""

        ahora = self._reloj()
        resultados: list[ResultadoEnvio] = []
        for canal, enviar in envios:
            if clave is not None and not self._datos.reservar_envio(org, clave, canal, ahora):
                continue
            try:
                r = enviar()
            except Exception:  # un canal roto no puede tumbar a los demás
                log.exception("aviso %s por %s: error inesperado (org %s)", tipo.value, canal, org)
                r = ResultadoEnvio(canal, False, "error-interno")
            if clave is not None and not r.ok:
                self._datos.liberar_envio(org, clave, canal)
            if not r.ok:
                log.warning("aviso %s por %s no salió (org %s): %s", tipo.value, canal, org, r.codigo)
            self._datos.registrar_historial_aviso(
                org, {"tipo": tipo.value, "canal": canal, "ok": r.ok, "codigo": r.codigo, **extra}, ahora
            )
            resultados.append(r)
        return resultados

    # --- avisos del motor -----------------------------------------------------------------------

    def notificar(self, evento: EventoAviso) -> None:
        """Llamado por el motor al escalar. No espera a la red ni lanza nunca."""

        try:
            self._ejecutor.submit(self._procesar, evento)
        except Exception:
            log.exception("no se pudo encolar el aviso de %s", evento.org)

    def _clave(self, e: EventoAviso) -> str:
        hora = _utc(e.en).strftime("%Y%m%d%H")
        if e.tipo == TipoAviso.presupuesto_agotado and (e.tope or "").startswith("mensual_usd"):
            return f"presupuesto/{e.workspace}/{e.tope}"  # una vez por mes y workspace
        return f"{e.tipo.value}/{e.workspace}/{e.unidad}/{e.fase}/{e.causa}/{e.tope or ''}/{hora}".replace(
            " ", "_"
        )

    def _procesar(self, e: EventoAviso) -> None:
        try:
            clave = self._clave(e)
            nuevo = self._datos.registrar_evento_aviso(
                e.org,
                clave,
                {
                    "tipo": e.tipo.value,
                    "workspace": e.workspace,
                    "unidad": e.unidad,
                    "fase": e.fase,
                    "causa": e.causa,
                },
                e.en,
            )
            if not nuevo:
                return
            config = self._datos.avisos_config(e.org)
            if config is None or not config.activo or not config.eventos.admite(e.tipo):
                return
            ahora = self._reloj()
            if self._datos.envios_desde(e.org, ahora - timedelta(hours=1)) >= MAX_AVISOS_HORA:
                log.warning("aviso de %s omitido: más de %d avisos en la última hora", e.org, MAX_AVISOS_HORA)
                return
            envios = self._por_canal(e.org, config, mensaje_evento(e, self._base), asunto(e))
            self._despachar(e.org, clave, e.tipo, envios, {"workspace": e.workspace, "unidad": e.unidad})
        except Exception:
            log.exception("aviso de %s: fallo al procesarlo", e.org)

    # --- informe --------------------------------------------------------------------------------

    def informe(self, org: str, hasta: datetime | None = None) -> dict[str, Any]:
        hasta = _utc(hasta or self._reloj())
        return construir_informe(self._almacen, self._datos, org, hasta - timedelta(days=7), hasta)

    def informe_semanal_pendiente(self) -> int:
        """Tarea del fondo: manda el informe de cada organización a la que le toca. Devuelve cuántos mandó."""

        ahora = self._reloj()
        enviados = 0
        for config in self._datos.avisos_con_informe():
            programado = instante_programado(ahora, config.informe.dia_semana, config.informe.hora_utc)
            if ahora - programado > timedelta(hours=VENTANA_INFORME_H):
                continue
            try:
                informe = construir_informe(
                    self._almacen, self._datos, config.org, programado - timedelta(days=7), programado
                )
                envios = self._por_canal(
                    config.org, config, mensaje_informe(informe, self._base), asunto_informe(informe)
                )
                clave = f"informe/{programado:%Y%m%d%H}"
                enviados += sum(
                    r.ok for r in self._despachar(config.org, clave, TipoAviso.informe_semanal, envios, {})
                )
            except Exception:
                log.exception("informe semanal de %s: fallo", config.org)
        return enviados

    # --- pruebas desde la consola ---------------------------------------------------------------

    def probar(self, org: str, que: str) -> list[ResultadoEnvio]:
        """Manda ahora un aviso de prueba o el informe de los últimos 7 días a los canales guardados."""

        config = self._datos.avisos_config(org)
        if config is None:
            raise ErrorAvisos("sin-configuracion", "guarda primero la configuración de avisos", 409)
        if que == "informe":
            informe = self.informe(org)
            mensaje, asunto_correo, tipo = (
                mensaje_informe(informe, self._base),
                asunto_informe(informe),
                TipoAviso.informe_semanal,
            )
        else:
            mensaje, asunto_correo, tipo = (
                mensaje_prueba(org, self._base),
                "[Railspec] Aviso de prueba",
                TipoAviso.prueba,
            )
        envios = self._por_canal(org, config, mensaje, asunto_correo)
        if not envios:
            raise ErrorAvisos(
                "sin-canales",
                "no hay ningún canal configurado y disponible (webhook de Teams o destinatarios con SMTP)",
                409,
            )
        return self._despachar(org, None, tipo, envios, {})
