"""railspec-graph: grafo de código centralizado y RAG de Railspec (repositorio central).

Capas, de abajo arriba:

- ``motor``: primitivas de un motor físico (``MotorFalkor`` hoy; ``MotorMemoria``
  como doble; LadybugDB entraría como otro ``MotorGrafo``).
- ``acceso``: único módulo que construye nombres de grafo e impone el filtro
  de workspace.
- ``almacen``: ``AlmacenGrafo`` (``GraphStore``) y ``AlmacenVectores``
  (``VectorStore``), con superposiciones por unidad.
- ``analitica``: clusters, procesos y riesgo de impacto; ``almacen`` añade la
  comparación base contra snapshot y las trazas ``CA-NN``.
- ``rag`` e ``ingesta``: recuperación por referencias e ingesta de snapshots.
- ``indexado``: ``graph.index``, el canónico por lotes desde CI; también barre
  lo abandonado (``AlmacenGrafo.limpiar_huerfanos``).
"""

from .acceso import AccesoGrafo, Espacio, FueraDeWorkspace, RepositorioNoVisible
from .almacen import AlmacenGrafo, AlmacenVectores, CodificadorConsulta, Huerfanos, Impacto
from .indexado import IndexadorCanonico, IndiceDesfasado, IndiceRechazado
from .ingesta import SnapshotRechazado, ingerir_snapshot
from .memoria import MotorMemoria
from .motor import MotorGrafo, Traza
from .rag import RecuperadorContexto

__version__ = "0.1.0"

__all__ = [
    "AccesoGrafo",
    "AlmacenGrafo",
    "AlmacenVectores",
    "CodificadorConsulta",
    "Espacio",
    "FueraDeWorkspace",
    "Huerfanos",
    "Impacto",
    "IndexadorCanonico",
    "IndiceDesfasado",
    "IndiceRechazado",
    "MotorGrafo",
    "MotorMemoria",
    "RecuperadorContexto",
    "RepositorioNoVisible",
    "SnapshotRechazado",
    "Traza",
    "ingerir_snapshot",
]
