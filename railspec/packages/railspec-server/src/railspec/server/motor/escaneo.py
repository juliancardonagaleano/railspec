"""Reescaneo de secretos de un snapshot en el servidor.

El contrato exige ``escaneo_secretos.hallazgos == 0``, pero lo declara el
cliente: el servidor no se fía y revisa el texto de código que sí le llega
(``diff`` y ``fragmentos[].texto``) con los mismos patrones que aplica el
proxy (``chat.secretos``, con prueba de paridad) y con su mismo criterio,
línea a línea. Un hallazgo invalida el snapshot entero. Se informa el tipo y
la ruta, nunca el valor.

Un falso positivo se corrige en el patrón, en los dos lados; no hay bypass.

El texto lo manda el cliente, así que ningún patrón puede costar más que lineal
en él. ``cadena-conexion`` y ``jwt`` lo eran (64 000 caracteres: de 1 a 9 s; un
diff de 2 MB hecho a propósito: media hora) y ``chat.secretos`` ya acota sus
cuantificadores. Como segunda barrera, por si algún patrón futuro no lo hace,
ninguna búsqueda ve más de ``VENTANA`` caracteres seguidos: las líneas más
largas se revisan por tramos solapados.
"""

from __future__ import annotations

from railspec.contracts.snapshot import Snapshot

from ..chat.secretos import detectar

#: Cuántos hallazgos se nombran en el error; el resto se cuenta.
MAX_INFORMADOS = 10
#: Caracteres que ve una búsqueda de patrón.
VENTANA = 1024
#: Un secreto de hasta ``SOLAPE`` caracteres cabe entero en algún tramo de una línea larga.
SOLAPE = 512


def hallazgos_de_secretos(snapshot: Snapshot) -> list[str]:
    """Un texto ``<tipo> en <lugar>`` por cada secreto detectado; vacío si el snapshot está limpio.

    En ``restringido`` el contrato prohíbe diff y fragmentos, así que no hay
    texto que revisar.
    """

    hallazgos: dict[str, None] = {}  # sin repetidos y en orden
    if snapshot.diff:
        tocadas = {a.ruta for a in snapshot.archivos} | {
            a.ruta_anterior for a in snapshot.archivos if a.ruta_anterior
        }
        ruta: str | None = None
        for linea in snapshot.diff.splitlines():
            # La ruta que se informa sale de los archivos del snapshot, no del texto del diff.
            if linea.startswith("diff --git "):
                ruta = None
            elif linea.startswith(("--- a/", "+++ b/")) and linea[6:] in tocadas:
                ruta = linea[6:]
            for tipo in _tipos(linea):
                hallazgos[f"{tipo} en el diff ({ruta})" if ruta else f"{tipo} en el diff"] = None
    for fragmento in snapshot.fragmentos or []:
        for linea in fragmento.texto.splitlines():
            for tipo in _tipos(linea):
                hallazgos[f"{tipo} en el fragmento {fragmento.ruta}"] = None
    return list(hallazgos)


def _tipos(linea: str) -> list[str]:
    """Tipos de secreto de una línea; las de más de ``VENTANA`` caracteres, por tramos solapados."""

    if len(linea) <= VENTANA:
        return detectar(linea)
    tipos: dict[str, None] = {}
    for inicio in range(0, len(linea) - SOLAPE, VENTANA - SOLAPE):
        tipos.update(dict.fromkeys(detectar(linea[inicio : inicio + VENTANA])))
    return list(tipos)


def resumen(hallazgos: list[str]) -> str:
    """Los hallazgos para un mensaje de error o de log, acotados."""

    visibles = "; ".join(hallazgos[:MAX_INFORMADOS])
    resto = len(hallazgos) - MAX_INFORMADOS
    return f"{visibles}; y {resto} más" if resto > 0 else visibles
