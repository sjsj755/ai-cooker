"""P2 检索层：BM25 + Chroma 向量 + RRF 融合 + 缺料/评分编排。"""

from app.retrieval.bm25 import BM25Corpus, tokenize
from app.retrieval.errors import RetrievalUnavailableError
from app.retrieval.fusion import rrf
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.missing import MissingIngredientsCalculator, MissingInfo
from app.retrieval.ranking import EMPTY_RESULT_NOTICE, RankResult, RankingService, get_ranking_service
from app.retrieval.scoring import DefaultScoringStrategy

__all__ = [
    "BM25Corpus",
    "DefaultScoringStrategy",
    "EMPTY_RESULT_NOTICE",
    "HybridRetriever",
    "MissingIngredientsCalculator",
    "MissingInfo",
    "RankResult",
    "RankingService",
    "RetrievalUnavailableError",
    "get_ranking_service",
    "rrf",
    "tokenize",
]
