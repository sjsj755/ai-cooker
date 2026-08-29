"""检索层异常：MySQL 等致命故障统一抛 RetrievalUnavailableError → 503。"""


class RetrievalUnavailableError(RuntimeError):
    """检索依赖（MySQL）不可用等致命故障；与 /health/ready 语义一致。"""
