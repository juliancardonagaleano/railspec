"""Canales de salida: webhook entrante de Teams y correo por SMTP.

Ninguno devuelve ni registra el contenido de un error de la red: el URL del webhook es un secreto
(lleva la firma en el camino o en la consulta) y un mensaje de excepción de la librería lo repetiría.
Cada fallo sale como un ``codigo`` estable (``http-404``, ``destino-no-permitido``, ``smtp-autenticacion``…).

- **Teams**: ``POST`` JSON con una tarjeta adaptable (el formato de los webhooks de «Flujos de trabajo»
  de Teams; los conectores antiguos de Office 365 también lo aceptan con 200). El destino pasa por
  ``contexto.destinos.PoliticaDestinos``: solo https, solo los hosts de Microsoft de ``HOSTS_TEAMS``, solo IP
  públicas (se comprueba la resuelta al conectar), sin redirecciones.
- **Correo**: ``smtplib`` contra el servidor que fija la plataforma con ``RAILSPEC_SMTP_URL``. Las
  organizaciones solo eligen destinatarios, nunca el servidor.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ..contexto.destinos import DestinoNoPermitido, PoliticaDestinos

log = logging.getLogger("railspec.avisos")

VARIABLE_SMTP = "RAILSPEC_SMTP_URL"

#: Hosts de los webhooks de Teams (Flujos de trabajo de Power Automate y conectores de Office 365).
HOSTS_TEAMS = frozenset(
    {"*.logic.azure.com", "*.api.powerplatform.com", "*.webhook.office.com", "outlook.office.com"}
)

TIMEOUT_S = 10.0
_SEGURIDADES = {"starttls", "ssl", "ninguna"}


@dataclass(frozen=True)
class Mensaje:
    """Un aviso reducido a lo que ven los dos canales."""

    titulo: str
    hechos: tuple[tuple[str, str], ...] = ()
    lineas: tuple[str, ...] = ()
    enlace: str | None = None

    def texto(self) -> str:
        partes = [self.titulo, ""]
        partes += [f"{k}: {v}" for k, v in self.hechos]
        if self.lineas:
            partes += ["", *self.lineas]
        if self.enlace:
            partes += ["", self.enlace]
        return "\n".join(partes) + "\n"


@dataclass(frozen=True)
class ResultadoEnvio:
    canal: str
    ok: bool
    #: ``None`` si salió; si no, un código estable (nunca el texto del error de la librería).
    codigo: str | None = None


# --- Teams ---------------------------------------------------------------------------------------


def politica_teams(resolver: Any | None = None) -> PoliticaDestinos:
    return PoliticaDestinos(hosts=HOSTS_TEAMS, resolver=resolver)


def tarjeta_teams(m: Mensaje) -> dict[str, Any]:
    cuerpo: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": m.titulo, "weight": "Bolder", "size": "Medium", "wrap": True}
    ]
    if m.hechos:
        cuerpo.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in m.hechos]})
    cuerpo += [{"type": "TextBlock", "text": linea, "wrap": True, "spacing": "Small"} for linea in m.lineas]
    contenido: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": cuerpo,
    }
    if m.enlace:
        contenido["actions"] = [{"type": "Action.OpenUrl", "title": "Abrir en Railspec", "url": m.enlace}]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": contenido,
            }
        ],
    }


class CanalTeams:
    nombre = "teams"

    def __init__(self, politica: PoliticaDestinos | None = None, transporte: Any | None = None) -> None:
        self._politica = politica or politica_teams()
        self._transporte = transporte

    def validar(self, url: str) -> str:
        """El host del webhook si el destino es admisible; ``DestinoNoPermitido`` si no."""

        return self._politica.validar_url(url)[0]

    async def _enviar(self, url: str, m: Mensaje) -> ResultadoEnvio:
        try:
            self._politica.validar_url(url)
        except DestinoNoPermitido:
            return ResultadoEnvio(self.nombre, False, "destino-no-permitido")
        try:
            async with self._politica.cliente_http(timeout_s=TIMEOUT_S, transporte=self._transporte) as c:
                r = await c.post(url, json=tarjeta_teams(m))
        except Exception:  # la excepción puede repetir el URL: no se registra ni se devuelve
            return ResultadoEnvio(self.nombre, False, "sin-conexion")
        if 200 <= r.status_code < 300:
            return ResultadoEnvio(self.nombre, True)
        return ResultadoEnvio(self.nombre, False, f"http-{r.status_code}")

    def enviar(self, url: str, m: Mensaje) -> ResultadoEnvio:
        """Bloqueante; se llama desde un hilo (no hay bucle de eventos en él)."""

        return asyncio.run(self._enviar(url, m))


# --- Correo --------------------------------------------------------------------------------------


class ErrorSmtp(ValueError):
    """``RAILSPEC_SMTP_URL`` mal formada. El mensaje nunca trae el valor (lleva la contraseña)."""


@dataclass(frozen=True)
class ConfigSmtp:
    host: str
    puerto: int
    seguridad: str = "starttls"
    usuario: str | None = None
    clave: str | None = field(default=None, repr=False)
    desde: str = ""

    @classmethod
    def desde_url(cls, url: str) -> ConfigSmtp:
        """``smtp://usuario:clave@host:587?desde=railspec@empresa.com`` (o ``smtps://`` para TLS directo).

        ``seguridad`` (``starttls`` por defecto con ``smtp://``, ``ssl`` con ``smtps://``, ``ninguna``
        solo para un relé interno sin credenciales). Usuario y clave van codificados como en un URL.
        """

        try:
            p = urlsplit(url.strip())
            puerto = p.port
            host = p.hostname
            consulta = parse_qs(p.query, keep_blank_values=False)
        except ValueError:
            raise ErrorSmtp(f"{VARIABLE_SMTP} no es un URL válido") from None
        if p.scheme not in ("smtp", "smtps") or not host:
            raise ErrorSmtp(f"{VARIABLE_SMTP} debe ser smtp://[usuario:clave@]host[:puerto]?desde=correo")
        seguridad = (consulta.get("seguridad") or ["ssl" if p.scheme == "smtps" else "starttls"])[0]
        if seguridad not in _SEGURIDADES or (p.scheme == "smtps" and seguridad != "ssl"):
            raise ErrorSmtp(f"{VARIABLE_SMTP}: seguridad debe ser starttls, ssl o ninguna (smtps es ssl)")
        usuario = unquote(p.username) if p.username else None
        clave = unquote(p.password) if p.password else None
        if (usuario is None) != (clave is None):
            raise ErrorSmtp(f"{VARIABLE_SMTP}: usuario y clave van juntos")
        if clave and seguridad == "ninguna":
            raise ErrorSmtp(f"{VARIABLE_SMTP}: no se envían credenciales sin cifrar (seguridad=ninguna)")
        desde = (consulta.get("desde") or [usuario or ""])[0].strip()
        if not desde or "@" not in desde or any(c in desde for c in "\r\n<> "):
            raise ErrorSmtp(f"{VARIABLE_SMTP}: falta desde=<correo remitente>")
        return cls(host, puerto or (465 if seguridad == "ssl" else 587), seguridad, usuario, clave, desde)

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> ConfigSmtp | None:
        crudo = (entorno.get(VARIABLE_SMTP) or "").strip()
        return cls.desde_url(crudo) if crudo else None


class CanalCorreo:
    nombre = "correo"

    def __init__(self, config: ConfigSmtp, *, contexto_tls: ssl.SSLContext | None = None) -> None:
        self._c = config
        self._tls = contexto_tls

    def enviar(self, destinatarios: list[str], asunto: str, m: Mensaje) -> ResultadoEnvio:
        if not destinatarios:
            return ResultadoEnvio(self.nombre, False, "sin-destinatarios")
        msg = EmailMessage()
        msg["From"] = self._c.desde
        msg["To"] = ", ".join(destinatarios)
        msg["Subject"] = asunto
        msg.set_content(m.texto())
        tls = self._tls or ssl.create_default_context()
        try:
            if self._c.seguridad == "ssl":
                servidor: smtplib.SMTP = smtplib.SMTP_SSL(
                    self._c.host, self._c.puerto, timeout=TIMEOUT_S, context=tls
                )
            else:
                servidor = smtplib.SMTP(self._c.host, self._c.puerto, timeout=TIMEOUT_S)
            with servidor as s:
                if self._c.seguridad == "starttls":
                    s.starttls(context=tls)
                if self._c.usuario and self._c.clave:
                    s.login(self._c.usuario, self._c.clave)
                s.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            return ResultadoEnvio(self.nombre, False, "smtp-autenticacion")
        except smtplib.SMTPRecipientsRefused:
            return ResultadoEnvio(self.nombre, False, "smtp-destinatarios")
        except (smtplib.SMTPException, ValueError):
            return ResultadoEnvio(self.nombre, False, "smtp-error")
        except OSError:  # incluye los fallos de conexión, de TLS y los tiempos de espera
            return ResultadoEnvio(self.nombre, False, "smtp-sin-conexion")
        return ResultadoEnvio(self.nombre, True)
