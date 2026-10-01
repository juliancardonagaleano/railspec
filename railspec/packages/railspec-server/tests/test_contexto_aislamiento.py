"""Proveedores de contexto: secretos con namespace por organización, destinos permitidos y cliente MCP."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from datetime import UTC, datetime

import pytest
from apoyo_motor import JULIAN, WS
from railspec.contracts.comun import AlcanceUnidad, GateFase, GobernanzaConsultada
from railspec.contracts.repositorio import Auditoria, ProveedorContexto, RolContexto
from railspec.server.contexto import ClientePce, ContextoConectable, FuenteContexto, ResolutorSecretos
from railspec.server.contexto.destinos import DestinoNoPermitido, PoliticaDestinos, ip_publica
from railspec.server.contexto.secretos import ReferenciaInvalida, SecretoNoDisponible, validar_referencia
from railspec.server.estado import almacen_en_memoria


def correr(coro):
    return asyncio.run(coro)


# --- secretos con namespace por organización -----------------------------------------------------------


def test_referencia_valida_solo_en_el_namespace_de_su_organizacion():
    assert validar_referencia("secret://acme--pce/api-key", "acme") == ("acme--pce", "api-key")
    ajenas = [
        ("secret://pce/api-key", "acme"),  # sin namespace
        ("secret://otra--pce/api-key", "acme"),  # de otra organización
        ("secret://acme-pce/api-key", "acme"),  # prefijo con un solo guion
        ("secret://acme--/api-key", "acme"),  # sin nombre
        ("secret://acme----pce/api-key", "acme"),
        ("secret://acme--pce/_oculta", "acme"),  # la clave empieza por alfanumérico
        ("secret://acme--pce/..", "acme"),
        ("secret://acme--b--x/k", "acme"),  # pertenece a la organización ``acme--b``
        ("secret://acme--pce/api-key/../x", "acme"),
        ("secret://acme--pce/api-key\n", "acme"),
        ("https://no-es-ref", "acme"),
    ]
    for ref, org in ajenas:
        with pytest.raises(ReferenciaInvalida):
            validar_referencia(ref, org)
    # Esa misma referencia sí es de ``acme--b``: el prefijo más largo manda y nunca es ambigua.
    assert validar_referencia("secret://acme--b--x/k", "acme--b") == ("acme--b--x", "k")


def test_resolutor_solo_lee_el_namespace_de_la_organizacion(tmp_path):
    for secreto, valor in (("acme--pce", "clave-de-acme"), ("otra--pce", "clave-de-otra")):
        (tmp_path / secreto).mkdir()
        (tmp_path / secreto / "token").write_text(valor + "\n")
    r = ResolutorSecretos(tmp_path, {"RAILSPEC_SECRETO_ACME__DOCS_TOKEN": "de-entorno"})
    assert r.resolver("secret://acme--pce/token", "acme") == "clave-de-acme"
    assert r.resolver("secret://acme--docs/token", "acme") == "de-entorno"
    # La referencia existe en el pool, pero es de otro tenant: ni el archivo ni el entorno se leen.
    for ref in ("secret://otra--pce/token", "secret://pce/token", "secret://acme--docs/../otra--pce/token"):
        with pytest.raises(SecretoNoDisponible):
            r.resolver(ref, "acme")
    with pytest.raises(SecretoNoDisponible):
        r.resolver("secret://acme--docs/token", "otra")


def test_variables_de_entorno_de_organizaciones_distintas_no_chocan():
    # La variable de ``acme--b--x``/``k`` (org ``acme--b``) es la misma que saldría de ``acme--b``/``_x_k``
    # (org ``acme``): por eso la clave no puede empezar por ``_`` y esa colisión no se puede fabricar.
    var = ResolutorSecretos.variable("acme--b--x", "k")
    assert var == ResolutorSecretos.variable("acme--b", "_x_k")
    r = ResolutorSecretos("/nonexistent", {var: "de-acme-b"})
    assert r.resolver("secret://acme--b--x/k", "acme--b") == "de-acme-b"
    for ref in ("secret://acme--b--x/k", "secret://acme--b/_x_k"):
        with pytest.raises(SecretoNoDisponible):
            r.resolver(ref, "acme")


# --- política de destinos ------------------------------------------------------------------------------


POLITICA = PoliticaDestinos(hosts=frozenset({"pce.acme.com", "*.mcp.acme.com"}))


def test_url_permitida_solo_https_a_hosts_de_la_allowlist():
    assert POLITICA.validar_url("https://pce.acme.com/mcp") == ("pce.acme.com", 443)
    assert POLITICA.validar_url("https://PCE.acme.com.:8443/mcp?x=1") == ("pce.acme.com", 8443)
    assert POLITICA.validar_url("https://a.b.mcp.acme.com/") == ("a.b.mcp.acme.com", 443)
    malas = [
        "http://pce.acme.com/mcp",
        "https://attacker.example/mcp",
        "https://mcp.acme.com/",  # el comodín no cubre el apex
        "https://pce.acme.com.attacker.example/",
        "https://xpce.acme.com/",
        "https://user@pce.acme.com/",
        "https://user:pw@pce.acme.com/",
        "https://pce.acme.com@attacker.example/",
        "https://pce.acme.com\\@attacker.example/",
        "https://pce.acme.com#frag",
        "https://pce.acme.com:99999/",
        "https://pce.acme.com:abc/",
        " https://pce.acme.com/",
        "https://pce.acme.com/\n",
        "https://pce.acme.com/a b",
        "https://127.0.0.1/mcp",
        "https://[::1]/mcp",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/",
        "https://localhost/",
        "ftp://pce.acme.com/",
        "//pce.acme.com/",
        "pce.acme.com",
        "https:///mcp",
        "",
    ]
    for url in malas:
        with pytest.raises(DestinoNoPermitido):
            POLITICA.validar_url(url)
    # Sin allowlist no se permite ningún destino (falla cerrado).
    with pytest.raises(DestinoNoPermitido):
        PoliticaDestinos().validar_url("https://pce.acme.com/mcp")


def test_ip_literal_solo_si_es_publica_y_esta_en_la_allowlist():
    p = PoliticaDestinos(hosts=frozenset({"93.184.216.34", "10.1.2.3", "127.0.0.1", "169.254.169.254"}))
    assert p.validar_url("https://93.184.216.34/mcp") == ("93.184.216.34", 443)
    for url in (
        "https://8.8.8.8/",  # pública, pero la plataforma no la permitió
        "https://10.1.2.3/",  # permitida, pero privada
        "https://127.0.0.1/",
        "https://169.254.169.254/latest/meta-data",
    ):
        with pytest.raises(DestinoNoPermitido):
            p.validar_url(url)


@pytest.mark.parametrize(
    ("ip", "publica"),
    [
        ("93.184.216.34", True),
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("::1", False),
        ("10.0.0.1", False),
        ("172.16.5.4", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("fe80::1", False),
        ("fe80::1%eth0", False),
        ("fc00::1", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("::", False),
        ("224.0.0.1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("64:ff9b::7f00:1", False),
        ("2002:7f00:1::", False),
        ("no-es-ip", False),
    ],
)
def test_ip_publica(ip, publica):
    assert ip_publica(ip) is publica


def _con_resolucion(ips_por_host):
    async def resolver(host, puerto):
        return ips_por_host[host]

    return PoliticaDestinos(hosts=frozenset({"pce.acme.com"}), resolver=resolver)


def test_la_ip_resuelta_se_comprueba_no_solo_el_nombre():
    publica = _con_resolucion({"pce.acme.com": ["93.184.216.34"]})
    assert correr(publica.resolver_publica("pce.acme.com", 443)) == ["93.184.216.34"]
    # El nombre está en la allowlist, pero el DNS lo manda a una IP interna: no se conecta.
    for ips in (["10.0.0.5"], ["93.184.216.34", "169.254.169.254"], ["::ffff:127.0.0.1"], []):
        with pytest.raises(DestinoNoPermitido):
            correr(_con_resolucion({"pce.acme.com": ips}).resolver_publica("pce.acme.com", 443))


def test_el_transporte_conecta_a_la_ip_validada_y_rechaza_las_demas():
    httpcore2 = pytest.importorskip("httpcore2")

    class BaseFalsa(httpcore2.AsyncNetworkBackend):
        def __init__(self):
            self.conexiones = []

        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            self.conexiones.append((host, port))
            raise httpcore2.ConnectError("sin red en las pruebas")

    from railspec.server.contexto.destinos import backend_verificado

    base = BaseFalsa()
    ok = backend_verificado(_con_resolucion({"pce.acme.com": ["93.184.216.34", "93.184.216.35"]}), base)
    with pytest.raises(httpcore2.ConnectError):
        correr(ok.connect_tcp("pce.acme.com", 443))
    # Conecta a la IP ya comprobada (no vuelve a resolver el nombre), probando cada una.
    assert base.conexiones == [("93.184.216.34", 443), ("93.184.216.35", 443)]
    base.conexiones.clear()
    interno = backend_verificado(_con_resolucion({"pce.acme.com": ["127.0.0.1"]}), base)
    with pytest.raises(httpcore2.ConnectError, match="no pública"):
        correr(interno.connect_tcp("pce.acme.com", 443))
    assert base.conexiones == []


def test_el_cliente_http_seguro_no_llega_a_loopback_aunque_el_host_este_permitido():
    pytest.importorskip("httpx2")
    import httpx2

    p = PoliticaDestinos(hosts=frozenset({"localhost"}))

    async def caso():
        async with p.cliente_http({"X-API-Key": "k"}) as http:
            assert http.follow_redirects is False
            with pytest.raises(httpx2.ConnectError, match="no pública"):
                await http.get("https://localhost:9/mcp")

    correr(caso())


def _certificado(tmp_path, nombre: str):
    """Certificado autofirmado para ``nombre``; devuelve (cert.pem, key.pem)."""

    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    clave = ec.generate_private_key(ec.SECP256R1())
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(minutes=5))
        .not_valid_after(ahora + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(nombre)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(clave, hashes.SHA256())
    )
    pem, llave = tmp_path / "cert.pem", tmp_path / "key.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    llave.write_bytes(
        clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return pem, llave


def test_el_cliente_http_seguro_conecta_por_la_ip_comprobada_y_verifica_el_nombre(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    uvicorn = pytest.importorskip("uvicorn")
    httpx2 = pytest.importorskip("httpx2")
    from railspec.server.contexto import destinos

    pem, llave = _certificado(tmp_path, "pce.test")
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    monkeypatch.setattr(destinos, "ip_publica", lambda ip: True)  # el servidor de la prueba está en loopback

    async def app(scope, receive, send):
        if scope["type"] == "http":
            cabeceras = dict(scope["headers"])
            cuerpo = b"clave=" + cabeceras.get(b"x-api-key", b"") + b" host=" + cabeceras[b"host"]
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": cuerpo})

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=puerto, log_level="error", ssl_certfile=str(pem), ssl_keyfile=str(llave)
    )
    servidor = uvicorn.Server(config)
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    for _ in range(200):
        if servidor.started:
            break
        time.sleep(0.05)
    assert servidor.started
    resueltos = []

    async def resolver(host, puerto_):
        resueltos.append(host)
        return ["127.0.0.1"]

    politica = PoliticaDestinos(hosts=frozenset({"pce.test", "otro.test"}), resolver=resolver)

    async def caso():
        async with politica.cliente_http({"X-API-Key": "k"}) as http:
            r = await http.get(f"https://pce.test:{puerto}/x")
            assert r.status_code == 200 and r.text == f"clave=k host=pce.test:{puerto}"
            # Se conecta por la IP ya comprobada, sin volver a resolver el nombre.
            assert resueltos == ["pce.test"]
        async with politica.cliente_http() as http:
            # El TLS se verifica contra el nombre de la URL, no contra la IP: otro nombre, otro certificado.
            with pytest.raises(httpx2.ConnectError):
                await http.get(f"https://otro.test:{puerto}/x")

    try:
        correr(caso())
    finally:
        servidor.should_exit = True
        hilo.join(5)


# --- el contexto conectable no filtra secretos de otro tenant -------------------------------------------


OTRA = "otra"
UNIDAD_B = AlcanceUnidad(org=OTRA, workspace=WS, unidad="0001-emitir-pdf")
UNIDAD_A = AlcanceUnidad(org="acme", workspace=WS, unidad="0001-emitir-pdf")


class ClienteVacio:
    async def buscar(self, consultas):
        return [[] for _ in consultas]


def _proveedor(org, url, ref, nombre="pce", workspace=None):
    ahora = datetime(2026, 9, 30, tzinfo=UTC)
    return ProveedorContexto(
        version=1,
        auditoria=Auditoria(creado_por=JULIAN, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora),
        org=org,
        workspace=workspace,
        rol=RolContexto.gobernanza,
        nombre=nombre,
        url=url,
        credencial_ref=ref,
        politica_fallo="estricta",
    )


def _conectable(proveedores, tmp_path, destinos, entorno=None, defecto=()):
    almacen = almacen_en_memoria()
    almacen.guardar_configuracion(proveedores)
    vistos = []

    def fabrica(f, clave):
        vistos.append((f.url, clave))
        return ClienteVacio()

    c = ContextoConectable(
        almacen,
        list(defecto),
        ResolutorSecretos(tmp_path, entorno or {}),
        fabrica=fabrica,
        destinos=destinos,
    )
    return c, vistos


def _secreto_de_a(tmp_path):
    (tmp_path / "pce-tenant-a").mkdir()
    (tmp_path / "pce-tenant-a" / "token").write_text("clave-del-tenant-a\n")
    (tmp_path / "acme--pce").mkdir()
    (tmp_path / "acme--pce" / "token").write_text("clave-del-tenant-a\n")


def test_el_proveedor_de_otro_tenant_no_recibe_la_clave_del_pool(tmp_path):
    _secreto_de_a(tmp_path)
    # Escenario del hallazgo: la org B apunta a su servidor con la referencia del secreto de A.
    atacante = _proveedor(OTRA, "https://attacker.example/mcp", "secret://pce-tenant-a/token")
    ajena = _proveedor(OTRA, "https://attacker.example/mcp", "secret://acme--pce/token")
    for p in (atacante, ajena):
        # Aun con el host permitido por la plataforma, la referencia no es del namespace de B.
        c, vistos = _conectable([p], tmp_path, PoliticaDestinos(hosts=frozenset({"attacker.example"})))
        r = correr(c.consultar(UNIDAD_B, GateFase.spec, "firmar pdf"))
        assert r.consultada == GobernanzaConsultada.no
        assert vistos == [] and "clave-del-tenant-a" not in r.detalle
        assert "referencia" in r.detalle
    # Y con una referencia legítima de B pero un host que la plataforma no permitió, no sale nada.
    (tmp_path / "otra--pce").mkdir()
    (tmp_path / "otra--pce" / "token").write_text("clave-de-b\n")
    propia = _proveedor(OTRA, "https://attacker.example/mcp", "secret://otra--pce/token")
    c, vistos = _conectable([propia], tmp_path, PoliticaDestinos(hosts=frozenset({"pce.acme.com"})))
    r = correr(c.consultar(UNIDAD_B, GateFase.spec, "firmar pdf"))
    assert (
        r.consultada == GobernanzaConsultada.no and vistos == [] and "RAILSPEC_PROVEEDORES_HOSTS" in r.detalle
    )
    # Sin allowlist (por defecto) tampoco.
    c, vistos = _conectable([propia], tmp_path, PoliticaDestinos())
    assert correr(c.consultar(UNIDAD_B, GateFase.spec, "x")).consultada == GobernanzaConsultada.no
    assert vistos == []


def test_el_proveedor_propio_con_host_y_secreto_permitidos_se_conecta(tmp_path):
    _secreto_de_a(tmp_path)
    ok = _proveedor("acme", "https://pce.acme.com/mcp", "secret://acme--pce/token")
    c, vistos = _conectable([ok], tmp_path, PoliticaDestinos(hosts=frozenset({"pce.acme.com"})))
    r = correr(c.consultar(UNIDAD_A, GateFase.spec, "firmar pdf"))
    assert r.consultada == GobernanzaConsultada.si
    assert vistos == [("https://pce.acme.com/mcp", "clave-del-tenant-a")]


def test_la_fuente_del_entorno_no_pasa_por_la_allowlist(tmp_path):
    # RAILSPEC_PCE_URL lo fija la plataforma: es de confianza aunque no esté en RAILSPEC_PROVEEDORES_HOSTS.
    defecto = FuenteContexto(
        rol=RolContexto.gobernanza, nombre="pce", url="https://pce.interno.corp/mcp", api_key="clave-global"
    )
    c, vistos = _conectable([], tmp_path, PoliticaDestinos(), defecto=[defecto])
    r = correr(c.consultar(UNIDAD_A, GateFase.spec, "x"))
    assert r.consultada == GobernanzaConsultada.si and vistos == [
        ("https://pce.interno.corp/mcp", "clave-global")
    ]


def test_politica_desde_entorno():
    p = PoliticaDestinos.desde_entorno({})
    assert p.hosts == frozenset()
    p = PoliticaDestinos.desde_entorno(
        {
            "RAILSPEC_PROVEEDORES_HOSTS": " PCE.acme.com, *.mcp.acme.com  docs.acme.com ",
            "RAILSPEC_PCE_URL": "https://pce.global.example/mcp",
        }
    )
    assert p.hosts == {"pce.acme.com", "*.mcp.acme.com", "docs.acme.com", "pce.global.example"}
    for malo in ("https://pce.acme.com", "pce.acme.com/x", "*", "*.com.", "a b@c", "*.*.acme.com", "-x.com"):
        with pytest.raises(ValueError, match="RAILSPEC_PROVEEDORES_HOSTS"):
            PoliticaDestinos.desde_entorno({"RAILSPEC_PROVEEDORES_HOSTS": malo})


# --- cliente MCP real (mcp 2.x) -------------------------------------------------------------------------


def _servidor_mcp():
    """Servidor MCP de pruebas con ``search_catalog`` en 127.0.0.1: (puerto, claves vistas, parar)."""

    uvicorn = pytest.importorskip("uvicorn")
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("pce-falso")

    @srv.tool()
    def search_catalog(query: str, type: str | None = None) -> str:
        """Busca en el catálogo."""
        fila = {"id": f"{type or 'x'}-1", "type": type or "x", "title": "Título", "summary": query}
        return json.dumps({"results": [fila]})

    app = srv.streamable_http_app()
    vistas: list[bytes | None] = []

    async def con_espia(scope, receive, send):
        if scope["type"] == "http":
            vistas.append(dict(scope["headers"]).get(b"x-api-key"))
        await app(scope, receive, send)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(con_espia, host="127.0.0.1", port=puerto, log_level="error", lifespan="on")
    servidor = uvicorn.Server(config)
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    for _ in range(200):
        if servidor.started:
            break
        time.sleep(0.05)
    assert servidor.started

    def parar():
        servidor.should_exit = True
        hilo.join(5)

    return puerto, vistas, parar


def test_cliente_pce_habla_mcp_con_la_clave_en_la_cabecera():
    pytest.importorskip("mcp")
    puerto, vistas, parar = _servidor_mcp()
    try:
        pce = ClientePce(f"http://127.0.0.1:{puerto}/mcp", "clave-1", timeout_s=20)
        r = correr(pce.buscar([("adr", "firmar pdf"), (None, "otra")]))
        assert [[i.id for i in fila] for fila in r] == [["adr-1"], ["x-1"]]
        assert r[0][0].resumen == "firmar pdf" and r[0][0].tipo == "adr"
        assert set(vistas) == {b"clave-1"}
        # Con política de destinos (proveedor de una organización), loopback no se alcanza ni permitido.
        seguro = ClientePce(
            f"https://127.0.0.1:{puerto}/mcp",
            "clave-2",
            timeout_s=20,
            destinos=PoliticaDestinos(hosts=frozenset({"127.0.0.1"})),
        )
        assert correr(seguro.buscar([("adr", "x")])) == [None]
        assert b"clave-2" not in vistas
    finally:
        parar()
