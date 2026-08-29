"""RRF 融合原语：两路召回共用同一量纲，禁止各自实现。"""

from collections.abc import Sequence


def rrf(ranks: Sequence[int], k: int, weight: float) -> float:
    """加权平均倒数排名：w * mean(1/(k+r))，rank 为 1 基正整数；空证据贡献 0。

    上界为 w/(k+1)（所有 rank=1 时取到）。BM25 项与向量项共用此函数，
    保证两路量纲严格一致（同一 k、同一权重语义）。
    """
    if not ranks:
        return 0.0
    return weight * sum(1.0 / (k + r) for r in ranks) / len(ranks)
