"""Lo que cambia entre POSIX y Windows, en un solo sitio.

El resto del proxy no pregunta por el sistema operativo: llama a estas funciones. Son tres cosas: el
candado exclusivo de archivo (``fcntl.flock`` en POSIX, ``msvcrt.locking`` en Windows), saber si un pid
sigue vivo (``os.kill(pid, 0)`` en POSIX; en Windows ``os.kill`` mataría el proceso) y si los bits de modo
de un archivo significan algo (en Windows ``chmod`` solo toca el atributo de solo lectura: la
privacidad de ``~/.config/railspec`` la da la ACL heredada de la carpeta del usuario).
"""

from __future__ import annotations

import os
import sys
import time

ES_WINDOWS = sys.platform == "win32"

#: Segundos entre intentos mientras otro proceso tiene el candado.
PAUSA_CANDADO_S = 0.05

if sys.platform == "win32":
    import ctypes
    import msvcrt

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5

    def intentar_candado(fd: int) -> bool:
        """Toma sin esperar un candado exclusivo del primer byte del archivo; ``False`` si está ocupado."""

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def soltar_candado(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:  # cerrar el descriptor lo suelta de todos modos
            pass

    def pid_vivo(pid: int) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        gestor = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not gestor:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # existe, pero de otro usuario
        try:
            codigo = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(gestor, ctypes.byref(codigo)):
                return True
            return codigo.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(gestor)

else:
    import fcntl

    def intentar_candado(fd: int) -> bool:
        """Toma sin esperar un candado exclusivo sobre el archivo; ``False`` si está ocupado."""

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def soltar_candado(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    def pid_vivo(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def tomar_candado(fd: int, espera_s: float | None = None) -> bool:
    """Candado exclusivo sobre ``fd``: espera hasta ``espera_s`` segundos (sin límite si es ``None``).

    Devuelve ``False`` si se agotó la espera. Se suelta con ``soltar_candado`` o al cerrar ``fd``.
    """

    limite = None if espera_s is None else time.monotonic() + espera_s
    while not intentar_candado(fd):
        if limite is not None and time.monotonic() >= limite:
            return False
        time.sleep(PAUSA_CANDADO_S)
    return True


def modos_posix() -> bool:
    """``True`` si los bits de modo de un archivo (0600…) controlan quién lo lee."""

    return not ES_WINDOWS


def forzar_utf8() -> None:
    """En Windows, consola y tuberías usan la página de códigos ANSI (cp1252…) salvo que se diga otra cosa.

    Los mensajes del proxy llevan acentos y los hooks reciben JSON en UTF-8 por stdin, así que se fija UTF-8
    en los tres flujos de texto. ``PYTHONUTF8`` no basta: el binario congelado ignora las ``PYTHON*``.
    """

    if not ES_WINDOWS:
        return
    for flujo in (sys.stdin, sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar is not None:
            try:
                reconfigurar(encoding="utf-8")
            except (OSError, ValueError):  # flujo ya cerrado o que no admite cambiar la codificación
                pass
