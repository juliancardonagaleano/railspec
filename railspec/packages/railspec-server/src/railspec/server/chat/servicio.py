"""Servicio del chat de contexto: conversaciones, bucle del agente, gate de salida e insumos.

Flujo de una pregunta:

1. La conversación pertenece a su autor y a un workspace; su nivel efectivo y
   su política son los más restrictivos de sus repositorios.
2. El modelo se elige antes de enviar nada: en ``restringido`` e ``interno``
   solo Foundry en una región de la zona de datos configurada.
3. El agente alterna pasos de modelo y llamadas a tools de lectura por la
   superficie ``chat`` del registro único, con la organización, el workspace
   y los repositorios fijados por el servidor, nunca por el modelo.
4. Lo que devuelven las tools en campos ``codigo_interno`` se convierte en
   huellas (Mongo, con el TTL de la conversación) y se audita; el texto no se
   guarda.
5. La respuesta final pasa el gate de salida. Si bloquea, la persona ve un
   aviso con las reglas y la respuesta del modelo se descarta; los bloqueos
   repetidos limitan la conversación.

Cada llamada a modelo deja telemetría (``fase=chat``) y auditoría con el hash
de lo enviado, igual que en el motor.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from railspec.contracts.chat import (
    Afirmacion,
    ConsumoFuga,
    Conversacion,
    LlamadaTool,
    MensajeChat,
    RespuestaChat,
    RolMensaje,
)
from railspec.contracts.comun import (
    Actor,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    Canal,
    NivelCodigo,
    Proveedor,
    TipoActor,
)
from railspec.contracts.insumo import Insumo, RepositorioInsumo
from railspec.contracts.repositorio import (
    EventoAuditoria,
    HostingChat,
    RegistroAuditoria,
    RequisitoRol,
    Rol,
    TelemetriaNodo,
    VinculoRepositorio,
)
from railspec.contracts.tools import CodigoError, InsumoGetEntrada, InsumoGetSalida, Superficie, resolver_tool

from ..motor.motor import ErrorNegocio
from ..proveedores import ErrorProveedor, PerfilInsatisfacible, PeticionModelo, Proveedores
from . import gate as gate_salida
from .agente import MAX_LLAMADAS_POR_PASO, PasoAgente, bloque_resultado, sistema
from .almacen import AlmacenChat
from .codigo import FuenteCodigo
from .normalizacion import HuellasContexto, Parametros

log = logging.getLogger("railspec.chat")

_JERARQUIA = [Rol.lector, Rol.desarrollador, Rol.workspace_admin, Rol.org_admin]
_RESTRICCION = [NivelCodigo.restringido, NivelCodigo.interno, NivelCodigo.abierto]


class ErrorChat(Exception):
    def __init__(self, codigo: str, detalle: str, estado_http: int) -> None:
        super().__init__(f"{codigo}: {detalle}")
        self.codigo = codigo
        self.detalle = detalle
        self.estado_http = estado_http


class InsumoBloqueado(ErrorChat):
    def __init__(self, reglas: list[str]) -> None:
        super().__init__("gate-salida", "el gate de salida no dejó exportar el insumo", 422)
        self.reglas = reglas


@dataclass(frozen=True)
class ConfigChat:
    #: Modelo por proveedor para el rol ``chat``.
    modelos: dict[Proveedor, str] = field(default_factory=lambda: {Proveedor.foundry: "claude-sonnet-5-5"})
    #: Regiones de Azure de la zona de datos (``RAILSPEC_CHAT_ZONA_DATOS``). Vacío = ningún modelo
    #: sirve a ``restringido``/``interno``: el chat se niega antes de enviar nada.
    zona_datos: frozenset[str] = frozenset()
    max_pasos: int = 6
    ttl: timedelta = timedelta(hours=72)
    bloqueos_para_limitar: int = 3
    max_tokens: int = 8000


@dataclass(frozen=True)
class Evento:
    """Un evento del flujo SSE de una pregunta."""

    nombre: str
    datos: dict[str, Any]


def _dump(modelo: BaseModel) -> dict[str, Any]:
    return json.loads(modelo.model_dump_json())


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def _valores(datos: Any, ruta: str) -> list[str]:
    """Valores de una ruta ``a.b[].c`` (la de ``ToolDef.campos_codigo_interno``)."""

    actuales = [datos]
    for parte in filter(None, ruta.split(".")):
        es_lista = parte.endswith("[]")
        clave = parte.removesuffix("[]")
        siguientes = []
        for a in actuales:
            v = a.get(clave) if isinstance(a, dict) else None
            if v is None:
                continue
            siguientes.extend(v if es_lista and isinstance(v, list) else [v])
        actuales = siguientes
    return [v for v in actuales if isinstance(v, str)]


def politica_efectiva(
    vinculos: list[VinculoRepositorio],
) -> tuple[NivelCodigo, gate_salida.PoliticaGate, Any]:
    nivel = min((v.nivel_codigo for v in vinculos), key=_RESTRICCION.index)
    politicas = [v.chat_contexto_codigo for v in vinculos]
    gate = gate_salida.PoliticaGate(
        n_tokens=min(p.huella_tokens_n for p in politicas),
        fragmentos_permitidos=all(p.fragmentos_en_respuesta for p in politicas),
        tope_conversacion=min(p.presupuesto_fuga_conversacion for p in politicas),
        tope_usuario_dia=min(p.presupuesto_fuga_usuario_dia for p in politicas),
    )
    listas = [set(p.modelos_permitidos) for p in politicas if p.modelos_permitidos]
    modelos = set.intersection(*listas) if listas else None
    azure = any(p.hosting == HostingChat.azure_zona_datos for p in politicas) or nivel != NivelCodigo.abierto
    return nivel, gate, {"modelos": modelos, "azure": azure}


class ServicioChat:
    def __init__(
        self,
        *,
        almacen: Any,
        chat: AlmacenChat,
        registro: Any,
        autorizador: Any,
        proveedores: Proveedores,
        fuente: FuenteCodigo | None = None,
        config: ConfigChat | None = None,
        reloj: Callable[[], datetime] | None = None,
        nuevo_id: Callable[[], UUID] | None = None,
    ) -> None:
        self.almacen = almacen
        self.chat = chat
        self.registro = registro
        self.autorizador = autorizador
        self.proveedores = proveedores
        self.fuente = fuente
        self.config = config or ConfigChat()
        self.reloj = reloj or (lambda: datetime.now(UTC))
        self.nuevo_id = nuevo_id or uuid.uuid4

    # --- utilidades ---------------------------------------------------------------------------

    @property
    def tools(self) -> list[Any]:
        return self.registro.tools(Superficie.chat)

    def _exigir_rol(self, actor: Actor, alcance: AlcanceWorkspace, minimo: Rol = Rol.lector) -> None:
        if actor.tipo != TipoActor.humano:
            raise ErrorChat(CodigoError.fuera_de_alcance.value, "el chat es solo para personas", 403)
        rol = self.autorizador.rol(actor, alcance.org, alcance.workspace)
        if rol is None or _JERARQUIA.index(rol) < _JERARQUIA.index(minimo):
            raise ErrorChat(
                CodigoError.fuera_de_alcance.value,
                f"hace falta rol {minimo.value} en {alcance.org}/{alcance.workspace}",
                403,
            )

    def _vinculos(self, conv: Conversacion) -> list[VinculoRepositorio]:
        a = conv.alcance
        vinculos = []
        for repo in conv.repositorios:
            v = self.almacen.vinculo(AlcanceRepositorio(org=a.org, workspace=a.workspace, repositorio=repo))
            if v is None:
                raise ErrorChat(CodigoError.no_encontrado.value, f"{repo} ya no está vinculado", 404)
            vinculos.append(v)
        return vinculos

    def _propia(self, actor: Actor, id_: UUID) -> tuple[Conversacion, dict[str, Any]]:
        encontrada = self.chat.conversacion(id_)
        # Una conversación ajena se responde igual que una inexistente: no se revela que existe.
        if encontrada is None or encontrada[1]["autor"] != actor.github_id:
            raise ErrorChat(CodigoError.no_encontrado.value, "conversación no encontrada", 404)
        conv, meta = encontrada
        self._exigir_rol(actor, conv.alcance)
        return conv, meta

    def _auditar(self, conv: Conversacion, evento: EventoAuditoria, actor: Actor, **campos: Any) -> None:
        self.almacen.registrar_auditoria(
            RegistroAuditoria(
                id=self.nuevo_id(),
                alcance=conv.alcance,
                evento=evento,
                actor=actor,
                en=self.reloj(),
                conversacion=conv.id,
                nivel_codigo=conv.nivel_efectivo,
                **campos,
            )
        )

    # --- conversaciones ---------------------------------------------------------------------------

    def crear(self, actor: Actor, alcance: AlcanceWorkspace, repositorios: list[str]) -> Conversacion:
        self._exigir_rol(actor, alcance)
        if not repositorios:
            repositorios = [
                v.alcance.repositorio for v in self.almacen.vinculos(alcance.org, alcance.workspace)
            ]
        repositorios = list(dict.fromkeys(repositorios))
        vinculos = []
        for repo in repositorios:
            v = self.almacen.vinculo(
                AlcanceRepositorio(org=alcance.org, workspace=alcance.workspace, repositorio=repo)
            )
            if v is None:
                raise ErrorChat(
                    CodigoError.no_encontrado.value, f"{repo} no está vinculado a {alcance.workspace}", 404
                )
            vinculos.append(v)
        if not vinculos:
            raise ErrorChat(
                CodigoError.no_encontrado.value, "el workspace no tiene repositorios vinculados", 404
            )
        nivel, politica, _ = politica_efectiva(vinculos)
        commits = {}
        if self.fuente is not None:
            for v in vinculos:
                c = self.fuente.commit_canonico(v)
                if c:
                    commits[v.alcance.repositorio] = c
        ahora = self.reloj()
        conv = Conversacion(
            id=self.nuevo_id(),
            alcance=alcance,
            repositorios=repositorios,
            autor=actor,
            nivel_efectivo=nivel,
            creada_en=ahora,
            expira_en=ahora + self.config.ttl,
            consumo_fuga=ConsumoFuga(caracteres=0, tope=politica.tope_conversacion),
            consumo_fuga_usuario=ConsumoFuga(
                caracteres=self.chat.fuga_usuario(alcance.org, actor.github_id, ahora),
                tope=politica.tope_usuario_dia,
            ),
        )
        self.chat.guardar_conversacion(
            conv, n_tokens=politica.n_tokens, commits=commits, autor_id=actor.github_id
        )
        return conv

    def obtener(self, actor: Actor, id_: UUID) -> tuple[Conversacion, list[MensajeChat]]:
        conv, _ = self._propia(actor, id_)
        return conv, self.chat.mensajes(id_)

    def marcar(self, actor: Actor, id_: UUID, mensaje_id: UUID, conservar: bool) -> MensajeChat:
        self._propia(actor, id_)
        m = next((m for m in self.chat.mensajes(id_) if m.id == mensaje_id), None)
        if m is None:
            raise ErrorChat(CodigoError.no_encontrado.value, "mensaje no encontrado", 404)
        if m.rol != RolMensaje.asistente or (conservar and not m.veredicto_gate.permitido):
            raise ErrorChat("no-conservable", "solo se conservan respuestas que el gate permitió", 409)
        m = m.model_copy(update={"conservar_en_insumo": conservar})
        self.chat.reemplazar_mensaje(MensajeChat.model_validate(_dump(m)))
        return m

    # --- modelo ----------------------------------------------------------------------------------

    async def _elegir(
        self, org: str, nivel: NivelCodigo, restricciones: dict[str, Any]
    ) -> tuple[Any, str, dict[str, Any]]:
        """Proveedor, modelo y campos extra de ``PeticionModelo`` (despliegue y región si existen).

        Compatible con ``Proveedores`` con o sin catálogo: si tiene ``refrescar`` se
        llama antes de elegir, y ``org`` solo se pasa si la firma lo acepta.
        """

        refrescar = getattr(self.proveedores, "refrescar", None)
        if refrescar is not None:
            await (refrescar(org) if "org" in inspect.signature(refrescar).parameters else refrescar())
        extra_elegir = {"org": org} if "org" in inspect.signature(self.proveedores.elegir).parameters else {}
        try:
            eleccion = self.proveedores.elegir(
                "chat", RequisitoRol(modelo=self.config.modelos), nivel, **extra_elegir
            )
        except PerfilInsatisfacible as exc:
            raise ErrorChat(CodigoError.perfil_insatisfacible.value, str(exc), 422) from exc
        if restricciones["modelos"] is not None and eleccion.modelo not in restricciones["modelos"]:
            raise ErrorChat(
                CodigoError.perfil_insatisfacible.value,
                f"{eleccion.modelo} no está entre los modelos permitidos para el chat en estos repositorios",
                422,
            )
        if restricciones["azure"]:
            region = getattr(eleccion, "region", None) or eleccion.proveedor.region
            if eleccion.proveedor.proveedor != Proveedor.foundry or not region or region == "global":
                raise ErrorChat(
                    CodigoError.perfil_insatisfacible.value,
                    "restringido/interno: el chat solo usa modelos de Azure con región fija",
                    422,
                )
            if region not in self.config.zona_datos:
                raise ErrorChat(
                    CodigoError.perfil_insatisfacible.value,
                    f"la región {region} no está en la zona de datos configurada para el chat",
                    422,
                )
        campos = {f.name for f in dataclasses.fields(PeticionModelo)}
        extra = {
            c: getattr(eleccion, c) for c in ("despliegue", "region") if c in campos and hasattr(eleccion, c)
        }
        return eleccion.proveedor, eleccion.modelo, extra

    def _registrar_llamada(self, conv: Conversacion, respuesta: Any, sha_enviado: str, paso: int) -> None:
        ahora = self.reloj()
        repo = conv.repositorios[0]
        u = respuesta.uso
        self.almacen.registrar_telemetria(
            TelemetriaNodo(
                id=self.nuevo_id(),
                org=conv.alcance.org,
                workspace=conv.alcance.workspace,
                repositorio=repo,
                conversacion=conv.id,
                nodo="chat.agente",
                fase="chat",
                proveedor=respuesta.proveedor,
                modelo=respuesta.modelo,
                tokens_entrada=u.tokens_entrada,
                tokens_salida=u.tokens_salida,
                tokens_cache_lectura=u.tokens_cache_lectura,
                tokens_cache_escritura=u.tokens_cache_escritura,
                costo_usd=u.costo_usd,
                duracion_ms=u.duracion_ms,
                en=ahora,
            )
        )
        self._auditar(
            conv,
            EventoAuditoria.llamada_modelo,
            _actor_agente(conv.autor),
            repositorio=repo,
            proveedor=respuesta.proveedor,
            modelo=respuesta.modelo,
            region=respuesta.region or "global",
            sha256_enviado=sha_enviado,
            detalle={"nodo": "chat.agente", "paso": paso},
        )

    # --- tools ----------------------------------------------------------------------------------

    def _argumentos(self, conv: Conversacion, tool: Any, argumentos: dict[str, Any]) -> dict[str, Any]:
        """Fija el alcance de la conversación: el modelo no elige workspace ni repositorio fuera de ella."""

        args = dict(argumentos)
        org, ws = conv.alcance.org, conv.alcance.workspace
        campos = tool.entrada.model_fields
        if "alcance" in campos:
            alcance = dict(args.get("alcance") or {})
            if campos["alcance"].annotation is AlcanceRepositorio:
                repo = alcance.get("repositorio") or conv.repositorios[0]
                if repo not in conv.repositorios:
                    raise ErrorChat(
                        CodigoError.fuera_de_alcance.value, f"{repo} no es de esta conversación", 403
                    )
                args["alcance"] = {"org": org, "workspace": ws, "repositorio": repo}
            else:
                args["alcance"] = {"org": org, "workspace": ws}
        if "unidad" in campos and campos["unidad"].annotation is AlcanceUnidad:
            unidad = dict(args.get("unidad") or {})
            args["unidad"] = {**unidad, "org": org, "workspace": ws}
        if "org" in campos:
            args["org"] = org
            if "workspace" in campos:
                args["workspace"] = ws
        if "repositorios" in campos and tool.nombre == "graph.query":
            pedidos = [r for r in args.get("repositorios") or [] if r in conv.repositorios]
            args["repositorios"] = pedidos or list(conv.repositorios)
        return args

    async def _ejecutar(
        self, conv: Conversacion, meta: dict[str, Any], llamada: Any, huellas: HuellasContexto
    ) -> tuple[str, LlamadaTool | None]:
        visibles = {t.nombre: t for t in self.tools}
        tool = resolver_tool(llamada.tool)
        if tool is None or tool.nombre not in visibles:
            return bloque_resultado(llamada.tool, {"detalle": "tool no disponible en el chat"}, False), None
        inicio = time.monotonic()
        try:
            args = self._argumentos(conv, tool, llamada.argumentos)
        except ErrorChat as exc:
            return bloque_resultado(tool.nombre, {"codigo": exc.codigo, "detalle": exc.detalle}, False), None
        r = await self.registro.invocar(tool.nombre, args, _actor_agente(conv.autor), Superficie.chat)
        fragmentos = 0
        if r.ok:
            textos = [t for ruta in tool.campos_codigo_interno() for t in _valores(r.cuerpo, ruta)]
            if textos:
                nuevas = HuellasContexto()
                p = Parametros(meta["n_tokens"])
                for texto in textos:
                    nuevas.agregar(texto, p)
                    self._auditar(
                        conv,
                        EventoAuditoria.lectura_codigo,
                        _actor_agente(conv.autor),
                        repositorio=args.get("alcance", {}).get("repositorio", conv.repositorios[0]),
                        sha256_enviado=_sha(texto),
                        detalle={"tool": tool.nombre, "caracteres": len(texto)},
                    )
                self.chat.agregar_huellas(conv.id, nuevas, conv.expira_en)
                huellas.unir([nuevas])
                fragmentos = len(textos)
            commits = r.cuerpo.get("commits") if tool.nombre == "graph.query" else None
            if tool.nombre == "code.read":
                commits = {args["alcance"]["repositorio"]: r.cuerpo["commit"]}
            if commits:
                self.chat.actualizar_commits(conv.id, commits)
                for repo, commit in commits.items():
                    meta["commits"].setdefault(repo, commit)
        registro = LlamadaTool(
            tool=tool.nombre,
            entrada_sha256=_sha(json.dumps(args, sort_keys=True, ensure_ascii=False)),
            duracion_ms=int((time.monotonic() - inicio) * 1000),
            fragmentos_leidos=fragmentos,
        )
        return bloque_resultado(tool.nombre, r.cuerpo, r.ok), registro

    # --- preguntas ----------------------------------------------------------------------------------

    def _historial(self, mensajes: list[MensajeChat]) -> str:
        lineas = []
        for m in mensajes[-20:]:
            if m.rol == RolMensaje.usuario:
                lineas.append(f"Persona: {m.pregunta}")
            elif m.respuesta is not None:
                lineas.append("Agente: " + " ".join(a.texto for a in m.respuesta.afirmaciones))
            else:
                lineas.append("Agente: (respuesta retenida por el gate de salida)")
        return "\n".join(lineas) or "(sin mensajes previos)"

    async def preguntar(self, actor: Actor, id_: UUID, pregunta: str) -> AsyncIterator[Evento]:
        """Valida, elige modelo y guarda la pregunta; devuelve el flujo de eventos.

        Los errores previos (permisos, conversación limitada, modelo no permitido)
        se lanzan aquí como ``ErrorChat``, antes de abrir el flujo, para que la
        API los devuelva con su código HTTP.
        """

        conv, meta = self._propia(actor, id_)
        if conv.limitada:
            raise ErrorChat(
                "conversacion-limitada", "la conversación quedó limitada por bloqueos repetidos", 429
            )
        if not pregunta.strip() or len(pregunta) > 8000:
            raise ErrorChat("pregunta-invalida", "la pregunta debe tener entre 1 y 8000 caracteres", 422)
        vinculos = self._vinculos(conv)
        nivel, politica, restricciones = politica_efectiva(vinculos)
        proveedor, modelo, extra = await self._elegir(conv.alcance.org, nivel, restricciones)
        previos = self.chat.mensajes(id_)
        m_usuario = MensajeChat(
            id=self.nuevo_id(),
            conversacion=conv.id,
            alcance=conv.alcance,
            rol=RolMensaje.usuario,
            autor=actor,
            creado_en=self.reloj(),
            pregunta=pregunta,
        )
        self.chat.agregar_mensaje(m_usuario, conv.expira_en)

        async def flujo() -> AsyncIterator[Evento]:
            yield Evento("pregunta", _dump(m_usuario))
            async for evento in self._bucle(
                conv, meta, actor, previos, pregunta, proveedor, modelo, politica, extra
            ):
                yield evento

        return flujo()

    async def _bucle(
        self,
        conv: Conversacion,
        meta: dict[str, Any],
        actor: Actor,
        previos: list[MensajeChat],
        pregunta: str,
        proveedor: Any,
        modelo: str,
        politica: gate_salida.PoliticaGate,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[Evento]:
        prompt_sistema = sistema(self.tools)
        huellas = self.chat.huellas(conv.id)
        resultados: list[str] = []
        llamadas: list[LlamadaTool] = []
        crudo: dict[str, Any] | None = None
        contexto = (
            f"Organización: {conv.alcance.org}. Workspace: {conv.alcance.workspace}. "
            f"Repositorios: {', '.join(conv.repositorios)}. Nivel: {conv.nivel_efectivo.value}."
        )
        for paso in range(1, self.config.max_pasos + 1):
            ultimo = paso == self.config.max_pasos
            yield Evento("progreso", {"paso": paso, "tool": None, "texto": "Pensando"})
            contenido = "\n\n".join(
                [
                    contexto,
                    "Historial:\n" + self._historial(previos),
                    f"Pregunta actual: {pregunta}",
                    *resultados,
                    f"Paso {paso} de {self.config.max_pasos}."
                    + (" Es el último: responde ya con `respuesta`, sin llamadas." if ultimo else ""),
                ]
            )
            peticion = PeticionModelo(
                rol="chat",
                modelo=modelo,
                sistema=prompt_sistema,
                contenido=contenido,
                esquema=PasoAgente,
                max_tokens=self.config.max_tokens,
                etiqueta="chat.agente",
                metadatos={"conversacion": str(conv.id)},
                **(extra or {}),
            )
            try:
                respuesta = await proveedor.completar(peticion)
            except ErrorProveedor as exc:
                log.warning("chat %s: el proveedor falló: %s", conv.id, exc)
                yield Evento(
                    "error", {"codigo": "error-proveedor", "detalle": "el modelo no respondió; reintenta"}
                )
                return
            self._registrar_llamada(conv, respuesta, _sha(prompt_sistema + "\x00" + contenido), paso)
            valor: PasoAgente = respuesta.valor
            if valor.respuesta is not None or not valor.llamadas or ultimo:
                crudo = valor.respuesta
                break
            for pedida in valor.llamadas[:MAX_LLAMADAS_POR_PASO]:
                yield Evento(
                    "progreso", {"paso": paso, "tool": pedida.tool, "texto": f"Consultando {pedida.tool}"}
                )
                bloque, registro = await self._ejecutar(conv, meta, pedida, huellas)
                resultados.append(bloque)
                if registro is not None:
                    llamadas.append(registro)
        if crudo is None:
            yield Evento(
                "error", {"codigo": "sin-respuesta", "detalle": "el agente no llegó a una respuesta"}
            )
            return
        async for evento in self._cerrar(
            conv, meta, actor, crudo, huellas, politica, llamadas, proveedor, modelo
        ):
            yield evento

    async def _cerrar(
        self,
        conv: Conversacion,
        meta: dict[str, Any],
        actor: Actor,
        crudo: Any,
        huellas: HuellasContexto,
        politica: gate_salida.PoliticaGate,
        llamadas: list[LlamadaTool],
        proveedor: Any,
        modelo: str,
    ) -> AsyncIterator[Evento]:
        ahora = self.reloj()
        consumo_usuario = self.chat.fuga_usuario(conv.alcance.org, actor.github_id, ahora)
        resultado = gate_salida.evaluar(
            crudo,
            huellas=huellas,
            politica=politica,
            visibilidad=gate_salida.Visibilidad(
                conv.alcance.org, conv.alcance.workspace, frozenset(conv.repositorios)
            ),
            consumo=gate_salida.Consumo(conv.consumo_fuga.caracteres, consumo_usuario),
            ahora=ahora,
        )
        v = resultado.veredicto
        mensaje = MensajeChat(
            id=self.nuevo_id(),
            conversacion=conv.id,
            alcance=conv.alcance,
            rol=RolMensaje.asistente,
            autor=_actor_agente(actor),
            creado_en=ahora,
            respuesta=resultado.respuesta,
            aviso_bloqueo=None if v.permitido else v.reglas_fallidas,
            veredicto_gate=v,
            llamadas_tool=llamadas,
            proveedor=proveedor.proveedor,
            modelo=modelo,
        )
        bloqueos = conv.bloqueos
        if v.permitido:
            consumo_usuario = self.chat.sumar_fuga_usuario(
                conv.alcance.org, actor.github_id, ahora, resultado.cargo_fuga
            )
        else:
            bloqueos += 1
            self._auditar(
                conv,
                EventoAuditoria.bloqueo_gate_salida,
                actor,
                detalle={"reglas": ",".join(r.value for r in v.reglas_fallidas), "mensaje": str(mensaje.id)},
            )
        conv = conv.model_copy(
            update={
                "consumo_fuga": ConsumoFuga(
                    caracteres=conv.consumo_fuga.caracteres + resultado.cargo_fuga,
                    tope=politica.tope_conversacion,
                ),
                "consumo_fuga_usuario": ConsumoFuga(
                    caracteres=consumo_usuario, tope=politica.tope_usuario_dia
                ),
                "bloqueos": bloqueos,
                "limitada": bloqueos >= self.config.bloqueos_para_limitar,
            }
        )
        conv = Conversacion.model_validate(_dump(conv))
        self.chat.guardar_conversacion(
            conv, n_tokens=meta["n_tokens"], commits=meta["commits"], autor_id=actor.github_id
        )
        self.chat.agregar_mensaje(mensaje, conv.expira_en)
        yield Evento("respuesta", _dump(mensaje))
        yield Evento("fin", {"conversacion": _dump(conv)})

    # --- insumos -------------------------------------------------------------------------------------

    def exportar(
        self,
        actor: Actor,
        id_: UUID,
        objetivo: str,
        restricciones: list[str] | None = None,
        preguntas_abiertas: list[str] | None = None,
    ) -> Insumo:
        conv, meta = self._propia(actor, id_)
        conservados = [
            m
            for m in self.chat.mensajes(id_)
            if m.rol == RolMensaje.asistente and m.conservar_en_insumo and m.respuesta is not None
        ]
        if not conservados:
            raise ErrorChat("sin-hallazgos", "marca al menos una respuesta para conservar en el insumo", 422)
        hallazgos = [a for m in conservados for a in m.respuesta.afirmaciones][:50]
        preguntas = list(
            dict.fromkeys(
                [
                    *(preguntas_abiertas or []),
                    *(p for m in conservados for p in m.respuesta.preguntas_abiertas),
                ]
            )
        )[:20]
        restricciones = list(dict.fromkeys(restricciones or []))[:20]
        try:
            # El texto de la persona también pasa el gate: un insumo nunca lleva código, en ningún nivel.
            extra = [Afirmacion(texto=t) for t in [objetivo, *restricciones] if t.strip()]
        except (ValidationError, ValueError) as exc:
            raise ErrorChat("insumo-invalido", str(exc)[:500], 422) from exc
        vinculos = self._vinculos(conv)
        _, politica, _ = politica_efectiva(vinculos)
        # El insumo no admite fragmentos en ningún nivel: se evalúa como restringido en forma de código.
        politica = gate_salida.PoliticaGate(
            n_tokens=politica.n_tokens,
            fragmentos_permitidos=False,
            tope_conversacion=politica.tope_conversacion,
            tope_usuario_dia=politica.tope_usuario_dia,
        )
        ahora = self.reloj()
        candidato = RespuestaChat.model_construct(
            afirmaciones=[*hallazgos, *extra], preguntas_abiertas=preguntas
        )
        resultado = gate_salida.evaluar(
            candidato,
            huellas=self.chat.huellas(id_),
            politica=politica,
            visibilidad=gate_salida.Visibilidad(
                conv.alcance.org, conv.alcance.workspace, frozenset(conv.repositorios)
            ),
            consumo=gate_salida.Consumo(0, 0),
            ahora=ahora,
            cobrar=False,
        )
        if not resultado.permitido:
            reglas = [r.value for r in resultado.veredicto.reglas_fallidas]
            self._auditar(
                conv,
                EventoAuditoria.bloqueo_gate_salida,
                actor,
                detalle={"reglas": ",".join(reglas), "insumo": True},
            )
            raise InsumoBloqueado(reglas)
        hallazgos = list(resultado.respuesta.afirmaciones[: len(hallazgos)])
        repositorios = []
        for v in vinculos:
            repo = v.alcance.repositorio
            commit = meta["commits"].get(repo) or (self.fuente.commit_canonico(v) if self.fuente else None)
            commit = commit or _commit_de_referencias(hallazgos, repo)
            if commit is None:
                raise ErrorChat(
                    "sin-commit-base", f"no se conoce el commit de {repo}: consulta su grafo antes", 409
                )
            repositorios.append(RepositorioInsumo(repositorio=repo, rol=v.rol, base_commit=commit))
        campos = dict(
            id=self.nuevo_id(),
            alcance=conv.alcance,
            repositorios=repositorios,
            autor=actor,
            creado_en=ahora,
            nivel_efectivo=conv.nivel_efectivo,
            conversacion=conv.id,
            objetivo=objetivo.strip(),
            hallazgos=hallazgos,
            preguntas_abiertas=preguntas,
            restricciones=restricciones,
            veredicto_gate=resultado.veredicto,
        )
        try:
            borrador = Insumo.model_construct(**campos, sha256="0" * 64)
            sha = hashlib.sha256(borrador.contenido_canonico()).hexdigest()
            insumo = Insumo.model_validate({**_dump_construido(borrador), "sha256": sha})
        except (ValidationError, ValueError) as exc:
            raise ErrorChat("insumo-invalido", str(exc)[:500], 422) from exc
        self.chat.guardar_insumo(insumo)
        return insumo


def manejador_insumo_get(chat: AlmacenChat):
    """``insumo.get`` (MCP, HTTP y chat): un insumo del workspace por id; el registro ya exigió ``lector``."""

    async def insumo_get(entrada: InsumoGetEntrada, actor: Actor) -> InsumoGetSalida:
        insumo = chat.obtener_insumo(entrada.alcance, entrada.id)
        if insumo is None:
            raise ErrorNegocio(CodigoError.no_encontrado, f"insumo {entrada.id} no existe en este workspace")
        return InsumoGetSalida(insumo=insumo)

    return insumo_get


def _dump_construido(modelo: Insumo) -> dict[str, Any]:
    return json.loads(modelo.model_dump_json())


def _commit_de_referencias(hallazgos: list[Afirmacion], repo: str) -> str | None:
    for a in hallazgos:
        for r in a.referencias:
            if getattr(r, "repositorio", None) == repo and getattr(r, "commit", None):
                return r.commit
    return None


def _actor_agente(humano: Actor) -> Actor:
    return Actor(tipo=TipoActor.agente, canal=Canal.consola, agente="chat", en_nombre_de=humano.github_id)
