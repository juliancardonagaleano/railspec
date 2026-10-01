"""GitHub simulado (``httpx.MockTransport``) para las pruebas de identidad y de roles por equipo."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
from railspec.server.consola.config import ConfigGithubApp

#: Credenciales de la GitHub App de Railspec que usan las pruebas.
APP = ConfigGithubApp("Iv1.x", "secreto")

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

    Atiende la comprobación de la GitHub App (``POST /applications/{client_id}/token``) y
    ``GET /user/teams``. Devuelve el cliente y la lista de rutas pedidas; ``usuarios`` se puede
    mutar para cambiar la membresía entre llamadas. Un token desconocido da 404 en la comprobación
    de la App (y 401 en ``/user/teams``).
    """

    llamadas: list[str] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(peticion.url.path)
        if peticion.method == "POST" and peticion.url.path == f"/applications/{APP.client_id}/token":
            token = json.loads(peticion.content)["access_token"]
            if token not in usuarios:
                return httpx.Response(404, json={"message": "Not Found"})
            login, github_id, _ = usuarios[token]
            cuerpo = {"app": {"client_id": APP.client_id}, "user": {"login": login, "id": github_id}}
            return httpx.Response(200, json=cuerpo)
        token = peticion.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in usuarios:
            return httpx.Response(401, json={"message": "Bad credentials"})
        if peticion.url.path == "/user/teams":
            return usuarios[token][2](peticion)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(responder)), llamadas
