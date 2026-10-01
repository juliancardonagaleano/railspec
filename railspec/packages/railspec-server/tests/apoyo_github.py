"""GitHub simulado (``httpx.MockTransport``) para las pruebas de identidad y de roles por equipo."""

from __future__ import annotations

from collections.abc import Callable

import httpx

#: Respuesta de ``GET /user/teams`` para un token.
RespuestaEquipos = Callable[[httpx.Request], httpx.Response]


def equipos_fijos(*ids: int) -> RespuestaEquipos:
    return equipos_paginados(list(ids))


def equipos_paginados(*paginas: list[int]) -> RespuestaEquipos:
    """Una página por argumento, encadenadas con la cabecera ``Link: rel="next"`` como hace GitHub."""

    def responder(peticion: httpx.Request) -> httpx.Response:
        n = int(peticion.url.params.get("page", "1"))
        cuerpo = [{"id": i, "slug": f"equipo-{i}"} for i in paginas[n - 1]]
        cabeceras = {}
        if n < len(paginas):
            cabeceras["Link"] = f'<https://api.github.com/user/teams?per_page=100&page={n + 1}>; rel="next"'
        return httpx.Response(200, json=cuerpo, headers=cabeceras)

    return responder


def sin_permiso(peticion: httpx.Request) -> httpx.Response:
    return httpx.Response(403, json={"message": "Resource not accessible by integration"})


def respuesta_inesperada(peticion: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"message": "no es una lista"})


def sin_red(peticion: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("sin red", request=peticion)


def github_simulado(
    usuarios: dict[str, tuple[str, int, RespuestaEquipos]],
) -> tuple[httpx.Client, list[str]]:
    """``token -> (login, github_id, respuesta de /user/teams)``.

    Devuelve el cliente y la lista de rutas pedidas; ``usuarios`` se puede
    mutar para cambiar la membresía entre llamadas. Un token desconocido da 401.
    """

    llamadas: list[str] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(peticion.url.path)
        token = peticion.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in usuarios:
            return httpx.Response(401, json={"message": "Bad credentials"})
        login, github_id, equipos = usuarios[token]
        if peticion.url.path == "/user":
            return httpx.Response(200, json={"login": login, "id": github_id})
        if peticion.url.path == "/user/teams":
            return equipos(peticion)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(responder)), llamadas
