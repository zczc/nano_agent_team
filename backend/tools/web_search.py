"""
网页搜索工具模块

本模块实现了统一的网页搜索接口，支持通过不同的搜索供应商（如 DuckDuckGo）检索互联网信息。
搜索结果包含标题、内容摘要和链接。

主要类：
    - SearchProvider: 搜索供应商抽象基类
    - DuckDuckGoProvider: 基于 duckduckgo-search 库的免费搜索实现
    - SearchTool: 统一的搜索工具类，实现了 BaseTool 接口

设计理念：
    - 供应商抽象：通过 SearchProvider 接口，未来可以轻松集成 Google, Bing 或 Serper 等付费 API。
    - 容错性：如果选定的供应商初始化失败，会自动记录日志并提示错误。
    - 动态配置：支持通过 `configure` 方法在运行时切换搜索供应商。
"""

import json
import requests
from typing import List, Dict, Any, Optional, Type
from abc import ABC, abstractmethod
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator, environment_guard, output_sanitizer
from backend.infra.config import Config
from backend.utils.logger import Logger


class SearchProvider(ABC):
    """
    搜索引擎供应商抽象基类
    定义了统一的 search 接口。
    """
    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        执行搜索
        
        Args:
            query: 搜索关键词
            max_results: 返回的最大结果数量
            
        Returns:
            List[Dict]: 搜索结果列表，每个字典包含 'title', 'body', 'href' 等字段
        """
        pass


class DuckDuckGoProvider(SearchProvider):
    """
    DuckDuckGo 搜索供应商
    
    使用开源的 `duckduckgo-search` (ddgs) 库进行搜索，无需 API Key。
    """
    def __init__(self):
        try:
            from ddgs import DDGS
            
            self.ddgs = DDGS
            self.available = True
        except ImportError:
            self.available = False
            Logger.error("DuckDuckGo search (duckduckgo-search) not installed. Run 'pip install ddgs'.")


    def search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """执行 DDG 文本搜索"""
        if not self.available:
            return [{"title": "Error", "body": "DuckDuckGo provider unavailable.", "href": "#"}]
        try:
            with self.ddgs() as ddgs:
                # results is an iterator of dicts
                return [r for r in ddgs.text(query, max_results=max_results)]
        except Exception as e:
            Logger.error(f"DuckDuckGo search error: {e}")
            return [{"title": "Error", "body": f"DDG Search failed: {str(e)}", "href": "#"}]


class SearchTool(BaseTool):
    """
    统一网页搜索工具
    
    Agent 通过此工具访问互联网。支持在初始化或运行时选择不同的搜索供应商。
    默认供应商由 Config.SEARCH_PROVIDER 定义。
    """
    def __init__(self, provider_name: Optional[str] = None):
        """
        初始化搜索工具
        
        Args:
            provider_name: 供应商名称（如 "duckduckgo"），默认为配置中的默认值
        """
        self._provider_name = provider_name or Config.SEARCH_PROVIDER
        self._provider = self._create_provider(self._provider_name)

    def _create_provider(self, name: str) -> SearchProvider:
        """工厂方法：根据名称创建供应商实例"""
        name = name.lower()
        if name == "duckduckgo":
            return DuckDuckGoProvider()
        
        # 默认回退到 DuckDuckGo
        Logger.warning(f"Unknown search provider '{name}', falling back to DuckDuckGo.")
        return DuckDuckGoProvider()

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return f"Search the internet for up-to-date information, news, or facts using {self._provider_name}."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        """定义工具参数：query (必填), max_results (可选)"""
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5).",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5
                }
            },
            "required": ["query"]
        }

    def configure(self, context: Dict[str, Any]):
        """
        动态配置搜索供应商
        
        Args:
            context: 包含 'search_provider' 键的配置字典
        """
        new_provider = context.get("search_provider")
        if new_provider and new_provider != self._provider_name:
            self._provider_name = new_provider
            self._provider = self._create_provider(new_provider)

    def get_status_message(self, **kwargs) -> str:
        query = kwargs.get('query', '')
        return f"\n\n🔍 正在通过 {self._provider_name} 搜索: {query}...\n"

    @schema_strict_validator
    # @output_sanitizer(max_length=4000)
    def execute(self, query: str, max_results: int = 5) -> str:
        """
        执行搜索并返回 JSON 字符串结果
        
        装饰器说明：
            - @schema_strict_validator: 校验 query 和 max_results
            - @output_sanitizer: 将结果列表转为 JSON 字符串并处理可能的超长输出
        """
        results = self._provider.search(query, max_results)
        return json.dumps(results, ensure_ascii=False)

