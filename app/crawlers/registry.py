"""采集器注册表：新增站点 = 写适配器类并注册。"""

from app.core.crawler import RecipeCrawler


class CrawlerRegistry:
    """按站点名注册 / 获取 RecipeCrawler 适配器。"""

    def __init__(self) -> None:
        self._crawlers: dict[str, RecipeCrawler] = {}

    def register(self, crawler: RecipeCrawler) -> RecipeCrawler:
        if not crawler.name:
            raise ValueError("crawler.name 不能为空")
        self._crawlers[crawler.name] = crawler
        return crawler

    def get(self, name: str) -> RecipeCrawler | None:
        return self._crawlers.get(name)

    def names(self) -> list[str]:
        return sorted(self._crawlers)


registry = CrawlerRegistry()
