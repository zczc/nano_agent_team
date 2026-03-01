"""
网页内容读取工具模块

新实现：原生 HTTP fetch + markdownify 转换，不依赖外部 API。
旧实现（JinaWebReaderTool）保留在文件底部作为 backup。

主要类：
    - WebReaderTool: 原生 fetch + HTML→Markdown 转换（默认）
    - JinaWebReaderTool: 基于 Jina Reader API 的旧实现（backup）
"""

import re
import requests
from typing import Dict, Any
from backend.tools.base import BaseTool
from backend.llm.decorators import schema_strict_validator
from backend.infra.config import Config
from backend.utils.logger import Logger

MAX_RESPONSE_SIZE = 5 * 1024 * 1024   # 5MB — hard cap (reject)
TRUNCATE_THRESHOLD = 1 * 1024 * 1024  # 1MB — truncation target for oversized pages
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)


def _html_to_markdown(html: str) -> str:
    """HTML → Markdown，去除 script/style 等噪音标签。"""
    from bs4 import BeautifulSoup
    import markdownify

    soup = BeautifulSoup(html, "html.parser")
    # 移除噪音标签
    for tag in soup.find_all(["script", "style", "noscript", "iframe",
                              "object", "embed", "meta", "link"]):
        tag.decompose()
    cleaned = str(soup)
    md = markdownify.markdownify(
        cleaned,
        heading_style="ATX",
        bullets="-",
        code_language="",
        strip=["img"],
    )
    # 压缩连续空行
    md = re.sub(r'\n{3,}', '\n\n', md)
    return md.strip()


def _extract_text(html: str) -> str:
    """HTML → 纯文本。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


class WebReaderTool(BaseTool):
    """网页内容读取工具 — 原生 HTTP fetch + HTML→Markdown 转换。

    参考 opencode webfetch 实现：
    - 直接 requests.get 抓取页面
    - 用 markdownify 将 HTML 转为 Markdown
    - 支持 markdown / text / html 三种输出格式
    - Cloudflare 403 自动重试（换 UA）
    - 5MB 大小限制，30s 超时
    """

    @property
    def name(self) -> str:
        return "web_reader"

    @property
    def description(self) -> str:
        return (
            "Fetch a web page and return its content as clean markdown (default), "
            "plain text, or raw HTML. Max 5MB, 30s timeout."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the web page to read.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "text", "html"],
                    "description": "Output format: markdown (default), text, or html.",
                    "default": "markdown",
                },
            },
            "required": ["url"],
        }

    def get_status_message(self, **kwargs) -> str:
        url = kwargs.get("url", "")
        return f"\n\n📖 Reading: {url[:60]}...\n"

    @schema_strict_validator
    def execute(self, url: str, format: str = "markdown") -> str:
        if not url.startswith("http"):
            return "Error: URL must start with http:// or https://"

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT,
                                stream=True, allow_redirects=True)

            # Cloudflare bot detection — retry with honest UA
            if resp.status_code == 403:
                cf = resp.headers.get("cf-mitigated", "")
                if "challenge" in cf.lower():
                    Logger.info("Cloudflare challenge detected, retrying with plain UA")
                    headers["User-Agent"] = "nano-agent-team/web_reader"
                    resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT,
                                        stream=True, allow_redirects=True)

            resp.raise_for_status()

            # 检查大小 — 超大响应先截断 HTML 再转换，而非硬拒绝
            content = resp.text
            content_type = resp.headers.get("content-type", "")
            is_html = "text/html" in content_type or "xhtml" in content_type
            truncated = False

            content_size = len(content.encode("utf-8", errors="ignore"))
            if content_size > MAX_RESPONSE_SIZE:
                # 截断原始 HTML/text 到 TRUNCATE_THRESHOLD
                content = content[:TRUNCATE_THRESHOLD]
                truncated = True
                Logger.info(f"[WebReader] Response truncated from {content_size} to ~{TRUNCATE_THRESHOLD} bytes")

            if format == "html":
                result = content
            elif format == "text":
                result = _extract_text(content) if is_html else content
            else:  # markdown (default)
                result = _html_to_markdown(content) if is_html else content

            if truncated:
                result = (f"[NOTE: Page content was truncated from {content_size // 1024}KB "
                          f"to ~{TRUNCATE_THRESHOLD // 1024}KB due to size limit. "
                          f"The content below may be incomplete.]\n\n{result}")

            return result

        except requests.exceptions.Timeout:
            return f"Error: Request timed out after {DEFAULT_TIMEOUT}s"
        except requests.exceptions.HTTPError as e:
            return f"Error: HTTP {e.response.status_code if e.response else '?'} - {e}"
        except Exception as e:
            return f"Error: {str(e)}"


# ---------------------------------------------------------------------------
# BACKUP: JinaWebReaderTool — 旧的 Jina Reader API 实现
# 保留供需要时切换回来使用。如需启用，将 import 处的 WebReaderTool 替换为此类。
# ---------------------------------------------------------------------------

class JinaWebReaderTool(BaseTool):
    """[BACKUP] 基于 Jina Reader API (r.jina.ai) 的网页读取工具。

    需要 Config.JINA_READER_KEY，无 key 时以匿名模式运行（有速率限制）。
    """

    def __init__(self):
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
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the web page to read.",
                },
            },
            "required": ["url"],
        }

    def get_status_message(self, **kwargs) -> str:
        url = kwargs.get("url", "")
        return f"\n\n📖 正在读取网页: {url[:50]}...\n"

    @schema_strict_validator
    def execute(self, url: str) -> str:
        if not url.startswith("http"):
            return "Error: Invalid URL. URL must start with http or https."

        target_url = f"{self.base_url}{url}"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        try:
            response = requests.get(target_url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as e:
            return f"Error: HTTP {response.status_code} - {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"
