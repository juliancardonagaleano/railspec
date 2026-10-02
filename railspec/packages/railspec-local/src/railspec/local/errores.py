"""Errores del proxy local.

Todos heredan de ``ErrorRailspec`` para que el servidor MCP local los
convierta en un resultado de error legible para el arnés, sin trazas.
"""

from __future__ import annotations

from railspec.contracts.tools import CodigoError, ErrorTool


class ErrorRailspec(Exception):
    """Error del proxy con un mensaje pensado para el arnés y el desarrollador."""


class ConfigInvalida(ErrorRailspec):
    pass


class SinConexion(ErrorRailspec):
    """El servidor no respondió; lo que se pueda se encola y se reintenta después."""


class ErrorServidor(ErrorRailspec):
    """Error de negocio devuelto por el servidor (``ErrorTool`` del contrato)."""

    def __init__(self, error: ErrorTool) -> None:
        super().__init__(f"{error.codigo.value}: {error.detalle}")
        self.error = error

    @property
    def codigo(self) -> CodigoError:
        return self.error.codigo


class RespuestaInvalida(ErrorRailspec):
    """El servidor respondió algo que no encaja en el contrato."""


#: Qué hacer cuando el servidor no acepta el token, si el proxy no sabe de dónde salió.
NOTA_TOKEN_GENERICA = (
    "Inicia sesión con `railspec login` (o revisa RAILSPEC_TOKEN si lo exportas): tiene que ser un token "
    "de usuario de la GitHub App de Railspec (un token rsc1 de la consola no vale en el arnés)."
)


class ServidorRechazo(ErrorRailspec):
    """El servidor rechazó la llamada con un texto en vez de un ``ErrorTool``: casi siempre la identidad.

    El servidor responde así cuando no acepta el token: falta, venció, no es de la GitHub App de Railspec o
    GitHub no pudo comprobarlo. El texto del servidor dice cuál de esas; ``nota`` dice qué hacer según de
    dónde salió el token (``credenciales.Sesion.nota``) y por defecto sugiere ``railspec login``.
    """

    def __init__(self, tool: str, texto: str, nota: str | None = None) -> None:
        super().__init__(
            f"El servidor rechazó {tool}: {texto or 'sin detalle'}. {nota or NOTA_TOKEN_GENERICA}"
        )
        self.texto = texto


class CredencialesInvalidas(ErrorRailspec):
    """El archivo de credenciales por usuario no se puede leer o no es seguro."""


class LoginFallido(ErrorRailspec):
    """``railspec login`` no consiguió el token: denegado, código vencido o App mal configurada."""


class RenovacionFallida(ErrorRailspec):
    """No se pudo renovar la sesión con el refresh token.

    ``definitiva``: el refresh token no vale (venció, ya se usó, se revocó la App) y reintentar no cambia
    nada; el mensaje dice qué hacer. Si no, es pasajera (sin red, servidor caído) y el proxy reintenta.
    """

    def __init__(self, mensaje: str, *, definitiva: bool = False) -> None:
        super().__init__(mensaje)
        self.definitiva = definitiva


class SecretosDetectados(ErrorRailspec):
    """El árbol de trabajo tiene secretos: el snapshot no se construye ni se sube."""

    def __init__(self, hallazgos: list[str]) -> None:
        lista = "\n".join(f"- {h}" for h in hallazgos)
        super().__init__(
            "Se detectaron posibles secretos en archivos de la unidad; el snapshot no se sube.\n"
            f"{lista}\nQuítalos (o muévelos a variables de entorno) y vuelve a reportar."
        )
        self.hallazgos = hallazgos


class FueraDeAlcance(ErrorRailspec):
    def __init__(self, rutas: list[str]) -> None:
        lista = "\n".join(f"- {r}" for r in rutas)
        super().__init__(
            "La orden no permite tocar estos archivos; revierte esos cambios antes de reportar:\n" + lista
        )
        self.rutas = rutas


class UnidadEnUso(ErrorRailspec):
    pass
