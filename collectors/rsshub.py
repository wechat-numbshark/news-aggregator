from collectors.rss import RSSCollector

# RSSHub 公共实例列表（按优先级排列，挂了自动换）
RSSHUB_INSTANCES = [
    "https://rsshub.app",
    "https://rss.shab.fun",
    "https://rsshub.rssforever.com",
    "https://hub.slarker.me",
]


class RSSHubCollector(RSSCollector):
    """
    RSSHub 路由采集器。
    自动拼接公共实例地址，支持财联社、微博、知乎、东方财富等。
    配置中填 rsshub_path 即可，如 /cls/telegraph
    """

    def __init__(self, source_config: dict):
        path = source_config["rsshub_path"]
        # 先用第一个实例，失败时 _try_instances 会轮换
        cfg = source_config.copy()
        cfg["url"] = f"{RSSHUB_INSTANCES[0]}{path}"
        super().__init__(cfg)
        self._path = path
        self._instance_idx = 0

    async def fetch(self):
        for i, base in enumerate(RSSHUB_INSTANCES):
            self.url = f"{base}{self._path}"
            result = await super().fetch()
            if result:
                return result
        return []
