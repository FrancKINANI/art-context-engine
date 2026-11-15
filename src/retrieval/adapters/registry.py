"""Which adapters run for which art form (§4.2).

The matrix is deliberately small and per-art-form. Widening it is how a context
engine becomes a breadth-first search that knows a little about everything,
which is the failure mode the spec warns about (§2).
"""

from __future__ import annotations

from src.models import WorkType
from src.retrieval.adapters.base import SourceAdapter
from src.retrieval.adapters.genius import GeniusAdapter
from src.retrieval.adapters.imslp import IMSLPAdapter
from src.retrieval.adapters.openalex import OpenAlexAdapter
from src.retrieval.adapters.poetry_foundation import PoetryFoundationAdapter
from src.retrieval.adapters.semantic_scholar import SemanticScholarAdapter
from src.retrieval.adapters.wikidata import WikidataAdapter
from src.retrieval.adapters.wikisource import WikisourceAdapter
from src.retrieval.adapters.wikipedia import WikipediaAdapter
from src.retrieval.http import Backend

MUSIC_ADAPTERS = [
    WikipediaAdapter, WikidataAdapter, OpenAlexAdapter,
    IMSLPAdapter, GeniusAdapter, SemanticScholarAdapter,
]
POETRY_ADAPTERS = [
    WikipediaAdapter, WikidataAdapter, OpenAlexAdapter,
    WikisourceAdapter, PoetryFoundationAdapter, SemanticScholarAdapter,
]

_FOR_TYPE = {
    WorkType.MUSIC: MUSIC_ADAPTERS,
    WorkType.POETRY: POETRY_ADAPTERS,
}


def build_adapters(backend: Backend, work_type: WorkType) -> list[SourceAdapter]:
    classes = _FOR_TYPE.get(work_type, [WikipediaAdapter, WikidataAdapter, SemanticScholarAdapter])
    return [cls(backend) for cls in classes]


def adapter_names(work_type: WorkType) -> list[str]:
    return [cls.name for cls in _FOR_TYPE.get(work_type, [])]
