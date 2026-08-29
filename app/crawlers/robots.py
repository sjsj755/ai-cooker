"""robots.txt 最小实现：Crawl-delay + Disallow/Allow（支持 * 通配与 $ 锚点）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


@dataclass
class _Rule:
    pattern: re.Pattern[str]
    allow: bool
    length: int


@dataclass
class RobotsRules:
    """按最长规则优先、Allow 优先于 Disallow 的简化语义判定。"""

    crawl_delay: float = 10.0
    rules: list[_Rule] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        delay = 10.0
        rules: list[_Rule] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    pass
            elif key in ("disallow", "allow") and value:
                rules.append(
                    _Rule(
                        pattern=_compile_path_pattern(value),
                        allow=key == "allow",
                        length=len(value),
                    )
                )
        return cls(crawl_delay=delay, rules=rules)

    def allowed(self, url: str) -> bool:
        """返回该 URL 是否允许抓取（默认允许；命中规则取最长者，Allow 优先）。"""
        parsed = urlparse(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        matches = [r for r in self.rules if r.pattern.search(path)]
        if not matches:
            return True
        best = max(matches, key=lambda r: (r.length, r.allow))
        return best.allow


def _compile_path_pattern(path: str) -> re.Pattern[str]:
    """robots 路径转正则：`*` 匹配任意字符，`$` 表示行尾锚点。"""
    anchored = path.endswith("$")
    if anchored:
        path = path[:-1]
    body = re.escape(path).replace(r"\*", ".*")
    return re.compile("^" + body + ("$" if anchored else ""))


async def fetch_robots(
    client: httpx.AsyncClient,
    url: str = "https://www.xiachufang.com/robots.txt",
) -> RobotsRules:
    """抓取并解析 robots.txt；失败向上抛，由调用方决定阻断。"""
    resp = await client.get(url)
    resp.raise_for_status()
    return RobotsRules.parse(resp.text)
