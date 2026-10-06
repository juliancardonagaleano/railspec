"""Con varios repositorios en una unidad rige el nivel más restrictivo, no el del primario."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from apoyo_motor import (
    BASE,
    JULIAN,
    ORG,
    REPO,
    WS,
    WS_ALCANCE,
    aprobar,
    avanzar,
    construir,
    entrada_start,
    reporte,
    vinculo,
)
from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo, Perfil, RolRepositorio
from railspec.contracts.estado import Decision
from railspec.contracts.snapshot import (
    CambioArchivo,
    DeltaIndice,
    EscaneoSecretos,
    EstadoArchivo,
    ModoDelta,
    MotorIndice,
    Snapshot,
)
from railspec.contracts.tools import CodigoError, RepositorioInicio
from railspec.server.motor import ErrorNegocio
from railspec.server.motor.gate import requisitos_del_gate
from railspec.server.motor.motor import triaje
from railspec.server.motor.nucleo import mas_restrictivo
from railspec.server.motor.perfiles import perfil_por_defecto, tope_gate

OTRO = "pagos-api"
RUTA = "src/pdf.py"
#: Línea que solo está en el diff: si aparece en una petición al modelo, salió texto de código.
MARCA = "def firmar_con_marca_secreta(pdf):"


def correr(coro):
    return asyncio.run(coro)


def vinculo_de(repositorio: str, nivel: NivelCodigo):
    v = vinculo(nivel)
    return v.model_copy(
        update={
            "alcance": AlcanceRepositorio(org=ORG, workspace=WS, repositorio=repositorio),
            "url": f"https://github.com/acme/{repositorio}",
            "rol": RolRepositorio.primario if repositorio == REPO else RolRepositorio.transversal,
        }
    )


def con_niveles(motor, **niveles: NivelCodigo) -> None:
    """Registra un vínculo por repositorio; ``niveles`` mapea el nombre (con ``_`` por ``-``) a su nivel."""

    motor.n.almacen.guardar_configuracion(
        [vinculo_de(nombre.replace("_", "-"), nivel) for nombre, nivel in niveles.items()]
    )


def entrada_dos_repos(**extra):
    return entrada_start(
        repositorios=[
            RepositorioInicio(repositorio=REPO, rama="main", base_commit=BASE),
            RepositorioInicio(repositorio=OTRO, rama="main", base_commit=BASE),
        ],
        **extra,
    )


def snapshot_con_diff(orden, nivel: NivelCodigo) -> Snapshot:
    diff = (
        f"diff --git a/{RUTA} b/{RUTA}\nnew file mode 100644\nindex 0000000..1111111\n"
        f"--- /dev/null\n+++ b/{RUTA}\n@@ -0,0 +1,2 @@\n+{MARCA}\n+    return pdf\n"
    )
    return Snapshot(
        id=uuid.uuid4(),
        unidad=orden.unidad,
        repositorio=orden.repositorio,
        base_commit=orden.base_commit,
        hash_arbol="a" * 40,
        creado_en=datetime(2026, 9, 30, 20, tzinfo=UTC),
        nivel_codigo=nivel,
        modo_delta=ModoDelta.completo,
        archivos=[
            CambioArchivo(
                ruta=RUTA,
                estado=EstadoArchivo.agregado,
                sha256_despues=hashlib.sha256(RUTA.encode()).hexdigest(),
            )
        ],
        diff=diff,
        delta_indice=DeltaIndice(motor=MotorIndice(version="1.0.0")),
        escaneo_secretos=EscaneoSecretos(herramienta="railspec-secretos", version="0.1.0", hallazgos=0),
    )


async def hasta_cerrada(motor, alcance, nivel_snapshot: NivelCodigo) -> None:
    for _ in range(40):
        av = await avanzar(motor, alcance)
        if av.tipo == "cerrada":
            return
        if av.tipo == "orden":
            r = reporte(av.orden)
            if av.orden.tipo == "implementar":
                r = r.model_copy(update={"snapshot": snapshot_con_diff(av.orden, nivel_snapshot)})
            await motor.report(r, JULIAN)
        else:
            await aprobar(motor, alcance, av.checkpoint.id, decision=Decision.aprobado)
    raise AssertionError("la unidad no se cerró")


def peticiones_con_diff(proveedor) -> list[str]:
    return [p.contenido for p in proveedor.peticiones if "Diff:" in p.contenido]


def test_mas_restrictivo_ordena_y_sin_niveles_es_restringido():
    assert mas_restrictivo([NivelCodigo.abierto, NivelCodigo.interno]) == NivelCodigo.interno
    assert mas_restrictivo([NivelCodigo.abierto, NivelCodigo.interno, NivelCodigo.restringido]) == (
        NivelCodigo.restringido
    )
    assert mas_restrictivo([NivelCodigo.abierto]) == NivelCodigo.abierto
    assert mas_restrictivo([]) == NivelCodigo.restringido


@pytest.mark.parametrize(
    ("primario", "transversal", "efectivo"),
    [
        (NivelCodigo.abierto, NivelCodigo.restringido, NivelCodigo.restringido),
        (NivelCodigo.restringido, NivelCodigo.abierto, NivelCodigo.restringido),
        (NivelCodigo.abierto, NivelCodigo.interno, NivelCodigo.interno),
        (NivelCodigo.interno, NivelCodigo.abierto, NivelCodigo.interno),
        (NivelCodigo.abierto, NivelCodigo.abierto, NivelCodigo.abierto),
    ],
)
def test_nivel_efectivo_de_la_unidad(primario, transversal, efectivo):
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: primario, OTRO: transversal})
        estado = (await motor.start(entrada_dos_repos(), JULIAN)).estado
        assert motor.n.nivel(estado) == efectivo
        # El de un repositorio concreto sigue siendo el suyo (lo usa la comprobación del snapshot).
        assert motor.n.nivel(estado, REPO) == primario
        assert motor.n.nivel(estado, OTRO) == transversal

    correr(caso())


def test_un_repositorio_sin_vinculo_cuenta_como_restringido():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.abierto)  # solo el primario tiene vínculo
        estado = (await motor.start(entrada_dos_repos(), JULIAN)).estado
        assert motor.n.nivel(estado, REPO) == NivelCodigo.abierto
        assert motor.n.nivel(estado) == NivelCodigo.restringido

    correr(caso())


def test_unit_start_valida_el_perfil_con_las_claves_del_nivel_efectivo():
    """El nivel solo elige las claves ``rol:nivel`` del perfil; ``validar`` ya no lo recibe."""

    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.restringido})
        capturados = []
        validar = motor.n.proveedores.validar

        def espia(pares, **k):
            capturados.append(pares)
            assert "nivel" not in k and "zona" not in k
            return validar(pares, **k)

        motor.n.proveedores.validar = espia
        entrada = entrada_dos_repos()
        await motor.start(entrada, JULIAN)
        (pares,) = capturados
        perfil = perfil_por_defecto(WS_ALCANCE, Perfil.estandar)
        adversarial = tope_gate(perfil, triaje(entrada)).adversarial
        assert pares == requisitos_del_gate(perfil, NivelCodigo.restringido, adversarial)

    correr(caso())


def test_unit_start_rechaza_si_el_catalogo_no_sirve_el_perfil_sin_importar_el_nivel():
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.restringido})
        motor.n.proveedores.validar = lambda pares, **k: ["sin modelo"]
        with pytest.raises(ErrorNegocio) as exc:
            await motor.start(entrada_dos_repos(), JULIAN)
        assert exc.value.codigo == CodigoError.perfil_insatisfacible
        with pytest.raises(ErrorNegocio):  # tampoco con solo el primario abierto
            await motor.start(entrada_start(), JULIAN)

    correr(caso())


@pytest.mark.parametrize("nivel", list(NivelCodigo))
@pytest.mark.parametrize("region", [None, "global"])
def test_unit_start_arranca_en_cualquier_nivel_con_un_proveedor_sin_region_fija(nivel, region):
    async def caso():
        motor, proveedor = construir(nivel=nivel)
        proveedor.region = region
        salida = await motor.start(entrada_start(), JULIAN)
        assert salida.estado.nivel_efectivo == nivel

    correr(caso())


# --- Nivel congelado al crear la unidad (contrato 1.7) --------------------------------------------------


def test_unit_start_congela_el_nivel_de_cada_repositorio_y_el_efectivo():
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.interno})
        estado = (await motor.start(entrada_dos_repos(), JULIAN)).estado
        assert [(r.repositorio, r.nivel_codigo) for r in estado.repositorios] == [
            (REPO, NivelCodigo.abierto),
            (OTRO, NivelCodigo.interno),
        ]
        assert estado.nivel_efectivo == NivelCodigo.interno
        # Lo guardado coincide con lo devuelto.
        guardado = motor.n.leer(estado.unidad)
        assert guardado.nivel_efectivo == NivelCodigo.interno
        assert [r.nivel_codigo for r in guardado.repositorios] == [NivelCodigo.abierto, NivelCodigo.interno]

    correr(caso())


def test_un_repositorio_sin_vinculo_se_congela_como_restringido():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.abierto)  # solo el primario tiene vínculo
        estado = (await motor.start(entrada_dos_repos(), JULIAN)).estado
        assert [r.nivel_codigo for r in estado.repositorios] == [NivelCodigo.abierto, NivelCodigo.restringido]
        assert estado.nivel_efectivo == NivelCodigo.restringido

    correr(caso())


def test_cambiar_el_nivel_del_vinculo_no_altera_la_unidad_en_curso():
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.interno, OTRO: NivelCodigo.restringido})
        estado = (await motor.start(entrada_dos_repos(), JULIAN)).estado
        # Un org-admin relaja ambos repositorios en la consola con la unidad abierta.
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.abierto})
        assert motor.n.nivel_de(WS_ALCANCE, REPO) == NivelCodigo.abierto  # el vínculo vivo cambió
        assert motor.n.nivel(estado) == NivelCodigo.restringido
        assert motor.n.nivel(estado, REPO) == NivelCodigo.interno
        assert motor.n.nivel(estado, OTRO) == NivelCodigo.restringido

    correr(caso())


def test_el_gate_y_la_auditoria_usan_el_nivel_congelado_aunque_el_vinculo_cambie():
    async def caso():
        motor, proveedor = construir()
        con_niveles(motor, **{REPO: NivelCodigo.interno, OTRO: NivelCodigo.restringido})
        alcance = (await motor.start(entrada_dos_repos(), JULIAN)).estado.unidad
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.abierto})
        await hasta_cerrada(motor, alcance, NivelCodigo.interno)  # el snapshot declara el nivel congelado
        codigo = [p.contenido for p in proveedor.peticiones if "Nivel de código:" in p.contenido]
        assert codigo and all("Nivel de código: restringido." in c for c in codigo)
        assert not peticiones_con_diff(proveedor)
        filas = list(motor.n.almacen.db.auditoria.find({"evento": "llamada-modelo"}))
        assert filas and {f["nivel_codigo"] for f in filas} == {"restringido"}

    correr(caso())


def test_el_snapshot_se_comprueba_contra_el_nivel_congelado_no_contra_el_vinculo_vivo():
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.interno})
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        con_niveles(motor, **{REPO: NivelCodigo.abierto})
        for _ in range(40):
            av = await avanzar(motor, alcance)
            if av.tipo == "checkpoint":
                await aprobar(motor, alcance, av.checkpoint.id, decision=Decision.aprobado)
            elif av.orden.tipo == "implementar":
                break
            else:
                await motor.report(reporte(av.orden), JULIAN)
        r = reporte(av.orden)
        # Declarar el nivel nuevo del vínculo (abierto) ya no vale: la unidad nació interno.
        malo = r.model_copy(update={"snapshot": snapshot_con_diff(av.orden, NivelCodigo.abierto)})
        with pytest.raises(ErrorNegocio, match="la unidad fija nivel interno") as exc:
            await motor.report(malo, JULIAN)
        assert exc.value.codigo == CodigoError.snapshot_invalido
        bueno = r.model_copy(update={"snapshot": snapshot_con_diff(av.orden, NivelCodigo.interno)})
        await motor.report(bueno, JULIAN)

    correr(caso())


def test_una_unidad_anterior_a_1_7_sin_nivel_congelado_lee_el_vinculo_vivo():
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.interno, OTRO: NivelCodigo.interno})
        estado = (await motor.start(entrada_dos_repos(), JULIAN)).estado
        antigua = estado.model_copy(
            update={
                "nivel_efectivo": None,
                "repositorios": [r.model_copy(update={"nivel_codigo": None}) for r in estado.repositorios],
            }
        )
        assert motor.n.nivel(antigua) == NivelCodigo.interno
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.restringido})
        assert motor.n.nivel(antigua) == NivelCodigo.restringido  # sigue al vínculo, como hasta 1.6
        assert motor.n.nivel(antigua, REPO) == NivelCodigo.abierto
        assert motor.n.nivel(estado) == NivelCodigo.interno  # la congelada, no

    correr(caso())


def test_el_diff_del_primario_interno_no_sale_si_el_transversal_es_restringido():
    async def caso():
        motor, proveedor = construir()
        con_niveles(motor, **{REPO: NivelCodigo.interno, OTRO: NivelCodigo.restringido})
        alcance = (await motor.start(entrada_dos_repos(), JULIAN)).estado.unidad
        await hasta_cerrada(motor, alcance, NivelCodigo.interno)
        codigo = [p.contenido for p in proveedor.peticiones if "Nivel de código:" in p.contenido]
        assert codigo, "el gate de código debió llamar al modelo"
        assert all("Nivel de código: restringido." in c for c in codigo)
        assert not peticiones_con_diff(proveedor)
        assert not any(MARCA in p.contenido for p in proveedor.peticiones)

    correr(caso())


def test_con_todos_los_repos_internos_el_diff_si_sale():
    async def caso():
        motor, proveedor = construir()
        con_niveles(motor, **{REPO: NivelCodigo.interno, OTRO: NivelCodigo.interno})
        alcance = (await motor.start(entrada_dos_repos(), JULIAN)).estado.unidad
        await hasta_cerrada(motor, alcance, NivelCodigo.interno)
        con_marca = peticiones_con_diff(proveedor)
        assert con_marca and all(MARCA in c and "Nivel de código: interno." in c for c in con_marca)

    correr(caso())


def test_la_auditoria_de_cada_llamada_lleva_el_nivel_efectivo():
    async def caso():
        motor, _ = construir()
        con_niveles(motor, **{REPO: NivelCodigo.abierto, OTRO: NivelCodigo.restringido})
        alcance = (await motor.start(entrada_dos_repos(), JULIAN)).estado.unidad
        await hasta_cerrada(motor, alcance, NivelCodigo.abierto)
        filas = list(motor.n.almacen.db.auditoria.find({"evento": "llamada-modelo"}))
        assert filas
        assert {f["nivel_codigo"] for f in filas} == {"restringido"}

    correr(caso())


def test_un_repo_unico_abierto_sigue_enviando_el_diff():
    async def caso():
        motor, proveedor = construir(nivel=NivelCodigo.abierto)
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        await hasta_cerrada(motor, alcance, NivelCodigo.abierto)
        assert any(MARCA in c for c in peticiones_con_diff(proveedor))

    correr(caso())
