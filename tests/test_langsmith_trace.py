"""P5 LangSmith --trace：无 key 跳过 / 有 key 包装 / 关闭时原样透传。"""

from app.config import Settings
from app.core.langsmith_trace import maybe_trace


def _identity(x):
    return x


def test_maybe_trace_noop_without_flag():
    assert maybe_trace(_identity, "eval_retrieval", trace=False) is _identity


def test_maybe_trace_skips_without_key(monkeypatch, capsys):
    monkeypatch.setattr(
        "app.core.langsmith_trace.get_settings",
        lambda: Settings(langsmith_api_key=None),
    )
    assert maybe_trace(_identity, "eval_retrieval", trace=True) is _identity
    err = capsys.readouterr().err
    assert "LANGSMITH_API_KEY" in err


def test_maybe_trace_wraps_with_key(monkeypatch):
    monkeypatch.setattr(
        "app.core.langsmith_trace.get_settings",
        lambda: Settings(langsmith_api_key="test-key"),
    )
    wrapped = maybe_trace(_identity, "eval_retrieval", trace=True)
    assert callable(wrapped)
    assert wrapped is not _identity
