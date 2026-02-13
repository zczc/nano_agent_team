"""
网页内容读取工具模块

本模块实现了基于 Jina Reader API 的网页内容提取工具。
它可以将网页 HTML 转换为干净的 Markdown 文本，方便 LLM 进一步分析。

主要类：
    - WebReaderTool: 实现 BaseTool 接口的网页读取工具。

设计理念：
    - 外部服务集成：使用 Jina Reader (r.jina.ai) 提供的强大 Markdown 转换能力。
    - 安全性：校验 URL 格式。
    - 容错性：处理网络请求异常并返回清晰的错误提示。
"""

import requests
from typing import Dict, Any
from backend.infra.config import Config
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator, environment_guard, output_sanitizer


class WebReaderTool(BaseTool):
    """
    网页内容读取工具
    
    调用 Jina Reader API 将指定 URL 的网页内容转化为 Markdown 格式。
    """
    def __init__(self):
        """
        初始化读取工具
        
        从配置中读取 JINA_READER_KEY。如果未提供 Key，API 可能以匿名模式运行（有速率限制）。
        """
        self.api_key = Config.JINA_READER_KEY
        self.base_url = "https://r.jina.ai/"

    @property
    def name(self) -> str:
        return "web_reader"

    @property
    def description(self) -> str:
        return "Read the content of a specific web page and return its markdown content."
   
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        """定义工具参数：url (必填)"""
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the web page to read."
                }
            },
            "required": ["url"]
        }
  
    def get_status_message(self, **kwargs) -> str:
        url = kwargs.get("url", "")
        # 只显示 URL 的前 50 个字符
        return f"\n\n📖 正在读取网页: {url[:50]}...\n"
  
    @schema_strict_validator
    # @output_sanitizer(max_length=8000)
    def execute(self, url: str) -> str:
        """
        执行网页读取
        
        Args:
            url: 目标网页的完整 URL
            
        Returns:
            str: 网页内容的 Markdown 字符串，或错误信息
        """
        if not url.startswith("http"):
            return "Error: Invalid URL. URL must start with http or https."
            
        target_url = f"{self.base_url}{url}"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        
        try:
            # 执行 GET 请求，超时时间设为 30 秒
            response = requests.get(target_url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as e:
            return f"Error: HTTP {response.status_code} - {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

