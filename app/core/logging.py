"""结构化日志：事件型 JSON 行，采集管线统一使用。"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """将日志记录输出为单行 JSON：{ts, level, logger, event, ...fields}。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """配置根 logger 为结构化输出；重复调用幂等。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: object,
) -> None:
    """记录带事件名与附加字段的结构化日志。"""
    logger.log(level, event, extra={"event_fields": fields})
