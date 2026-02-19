"""
网页搜索工具模块

支持两个搜索供应商：
    - ExaProvider: 通过 Exa MCP 公开端点搜索，免费无需 API Key（默认）
    - DuckDuckGoProvider: 基于 duckduckgo-search 库的免费搜索（备选）

SearchTool 默认使用 Exa，失败时自动 fallback 到 DuckDuckGo。
"""

import json
import requests
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator
from backend.infra.config import Config
from backend.utils.logger import Logger

# 外层硬超时（秒）
SEARCH_TIMEOUT = 15


class SearchProvider(ABC):
    """搜索引擎供应商抽象基类"""
    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        pass


class ExaProvider(SearchProvider):
    """Exa MCP 搜索供应商 — 免费公开端点，无需 API Key。"""

    MCP_URL = "https://mcp.exa.ai/mcp"

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": query,
                    "type": "auto",
                    "numResults": max_results,
                    "livecrawl": "fallback",
                },
            },
        }
        try:
            resp = requests.post(
                self.MCP_URL,
                json=payload,
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                },
                timeout=25,
            )
            resp.raise_for_status()

            # Exa 返回 SSE 格式：每行 "data: {json}"
            results = []
            for line in resp.text.splitlines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                content_list = (
                    data.get("result", {}).get("content", [])
                )
                if not content_list:
                    continue
                text = content_list[0].get("text", "")
                results = self._parse_exa_text(text)
                break

            if not results:
                return [{"title": "No results", "body": "Exa returned no results.", "href": "#"}]
            return results[:max_results]

        except Exception as e:
            Logger.error(f"Exa search error: {e}")
            raise  # 让上层 fallback

    @staticmethod
    def _parse_exa_text(text: str) -> List[Dict[str, Any]]:
        """将 Exa 返回的文本解析为统一的结果列表。

        Exa MCP 返回的 text 通常是 markdown 格式的搜索结果，
        每条包含 Title / URL / Content 等信息。
        """
        results = []
        current: Dict[str, Any] = {}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Title:"):
                if current.get("title"):
                    results.append(current)
                current = {"title": line[6:].strip(), "body": "", "href": "#"}
            elif line.startswith("URL:"):
                current["href"] = line[4:].strip()
            elif line.startswith("Content:"):
                current["body"] = line[8:].strip()
            elif current and line and not line.startswith("---"):
                # 追加到 body
                if current.get("body"):
                    current["body"] += " " + line
                else:
                    current["body"] = line
        if current.get("title"):
            results.append(current)

        # 如果解析不出结构化结果，把整段文本作为单条返回
        if not results and text.strip():
            results.append({"title": "Search Results", "body": text[:3000], "href": "#"})
        return results


class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo 搜索供应商 — 基于 ddgs 库，无需 API Key。"""

    def __init__(self):
        try:
            from ddgs import DDGS
            self.ddgs = DDGS
            self.available = True
        except ImportError:
            self.available = False
            Logger.error("duckduckgo-search not installed. Run 'pip install ddgs'.")

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        if not self.available:
            return [{"title": "Error", "body": "DuckDuckGo provider unavailable.", "href": "#"}]
        try:
            ddgs = self.ddgs(timeout=8)
            return [r for r in ddgs.text(query, max_results=max_results)]
        except Exception as e:
            Logger.error(f"DuckDuckGo search error: {e}")
            return [{"title": "Error", "body": f"DDG Search failed: {str(e)}", "href": "#"}]

class SearchTool(BaseTool):
    """统一网页搜索工具 — 默认 Exa，失败自动 fallback 到 DuckDuckGo。"""

    def __init__(self, provider_name: Optional[str] = None):
        self._provider_name = provider_name or Config.SEARCH_PROVIDER
        self._provider = self._create_provider(self._provider_name)
        self._fallback: Optional[SearchProvider] = None

    def _create_provider(self, name: str) -> SearchProvider:
        name = name.lower()
        if name == "exa":
            return ExaProvider()
        if name == "duckduckgo":
            return DuckDuckGoProvider()
        Logger.warning(f"Unknown search provider '{name}', using Exa.")
        return ExaProvider()

    def _get_fallback(self) -> SearchProvider:
        if self._fallback is None:
            # Exa 主 → DDG 备；DDG 主 → Exa 备
            if isinstance(self._provider, ExaProvider):
                self._fallback = DuckDuckGoProvider()
            else:
                self._fallback = ExaProvider()
        return self._fallback

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the internet for up-to-date information, news, or facts. "
            "Returns a list of results with title, body, and URL."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5).",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def configure(self, context: Dict[str, Any]):
        new_provider = context.get("search_provider")
        if new_provider and new_provider != self._provider_name:
            self._provider_name = new_provider
            self._provider = self._create_provider(new_provider)
            self._fallback = None

    def get_status_message(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        return f"\n\n🔍 Searching via {self._provider_name}: {query}...\n"

    @schema_strict_validator
    def execute(self, query: str, max_results: int = 5) -> str:
        """执行搜索，主供应商失败时自动 fallback。"""
        try:
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(self._do_search, query, max_results)
            try:
                results = future.result(timeout=SEARCH_TIMEOUT)
            except FuturesTimeoutError:
                Logger.warning(f"web_search timeout ({SEARCH_TIMEOUT}s): {query}")
                results = [{"title": "Error",
                            "body": f"Search timed out after {SEARCH_TIMEOUT}s.",
                            "href": "#"}]
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            Logger.error(f"web_search unexpected error: {e}")
            results = [{"title": "Error",
                        "body": f"Search failed: {str(e)}", "href": "#"}]
        return json.dumps(results, ensure_ascii=False)

    def _do_search(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        """主供应商搜索，异常时 fallback。"""
        try:
            return self._provider.search(query, max_results)
        except Exception as e:
            Logger.warning(f"{self._provider_name} failed ({e}), falling back...")
            try:
                return self._get_fallback().search(query, max_results)
            except Exception as e2:
                Logger.error(f"Fallback search also failed: {e2}")
                return [{"title": "Error",
                         "body": f"All search providers failed. Primary: {e}, Fallback: {e2}",
                         "href": "#"}]
