import asyncio
import glob
import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional
from backend.llm.decorators import schema_strict_validator

from browser_use import Browser, BrowserProfile
from browser_use.agent.service import Agent  # type: ignore[import-untyped]
from browser_use.agent.views import AgentHistoryList  # type: ignore[import-untyped]
from browser_use.llm.base import BaseChatModel  # type: ignore[import-untyped]
from browser_use.llm.openai.chat import ChatOpenAI  # type: ignore[import-untyped]

try:
    import nest_asyncio  # type: ignore[import]
except ImportError:  # pragma: no cover
    nest_asyncio = None

# Module-level flag to prevent repeated nest_asyncio.apply() calls
_nest_asyncio_applied = False

from backend.infra.config import Config
from backend.tools.base import BaseTool
from backend.utils.logger import Logger

DEFAULT_MAX_STEPS = 50


def _find_playwright_chromium() -> Optional[str]:
    """
    查找 Playwright 安装的 Chromium 可执行文件路径。
    
    仅检查文件是否存在，不启动浏览器或 Playwright server，
    因此可以安全地在 asyncio event loop 中调用。
    
    Returns:
        Playwright Chromium 路径，如果未安装则返回 None
    """
    system = platform.system()
    pw_root = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')

    if system == 'Darwin':
        if not pw_root:
            pw_root = '~/Library/Caches/ms-playwright'
        patterns = [
            f'{pw_root}/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
            f'{pw_root}/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium',
        ]
    elif system == 'Linux':
        if not pw_root:
            pw_root = '~/.cache/ms-playwright'
        patterns = [
            f'{pw_root}/chromium-*/chrome-linux/chrome',
        ]
    elif system == 'Windows':
        if not pw_root:
            pw_root = os.environ.get('LOCALAPPDATA', '') + r'\ms-playwright'
        patterns = [
            f'{pw_root}\\chromium-*\\chrome-win\\chrome.exe',
        ]
    else:
        return None

    for pattern in patterns:
        expanded = str(Path(pattern).expanduser())
        matches = glob.glob(expanded)
        if matches:
            matches.sort()
            if Path(matches[-1]).is_file():
                return matches[-1]

    return None


def check_browser_installed() -> bool:
    """检查 Playwright Chromium 是否已安装。"""
    return _find_playwright_chromium() is not None


class BrowserUseTool(BaseTool):
    """A bridge to the browser-use agent for executing multi-step browsing tasks."""

    def __init__(self, get_model_key_fn=None):
        """
        Args:
            get_model_key_fn: Optional callable that returns the current model key string.
                              Injected from TUI layer to avoid backend -> src dependency.
        """
        self._get_model_key_fn = get_model_key_fn

    # ── properties ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "browser_use"

    @property
    def description(self) -> str:
        return (
            "Perform automated tasks using a browser (powered by the browser-use library). Suitable for complex tasks requiring real browser interactions, such as clicking, scrolling, handling dynamic content, multi-page navigation, and extracting structured data."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Describe the browsing task the agent should perform.",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum agent steps before giving up.",
                    "minimum": 1,
                    "default": DEFAULT_MAX_STEPS,
                },
            },
            "required": ["task"],
        }

    def get_status_message(self, **kwargs) -> str:
        task = kwargs.get("task", "")
        return f"\n\n🌐 Starting browser task: {task[:50]}...\n"

    # ── LLM creation (per-execution) ────────────────────────────

    def _resolve_model_key(self) -> Optional[str]:
        """Read current provider/model key, preferring injected callback."""
        if self._get_model_key_fn:
            try:
                key = self._get_model_key_fn()
                if key:
                    Logger.info(f"[BrowserUseTool] Using model from injected callback: {key}")
                    return key
            except Exception as e:
                Logger.debug(f"[BrowserUseTool] Injected callback failed: {e}")

        # Fallback: read active model directly from Config
        # (covers the case when instantiated via ToolRegistry without callback)
        try:
            if Config.ACTIVE_PROVIDER and Config.ACTIVE_MODEL:
                key = f"{Config.ACTIVE_PROVIDER}/{Config.ACTIVE_MODEL}"
                Logger.info(f"[BrowserUseTool] Using model from Config fallback: {key}")
                return key
        except Exception as e:
            Logger.debug(f"[BrowserUseTool] Config fallback failed: {e}")

        return None

    @staticmethod
    def _create_llm(config: Dict[str, Any]) -> Optional[BaseChatModel]:
        """Build a ChatOpenAI instance from a provider config dict."""
        model = config.get("model")
        api_key = config.get("api_key")
        if not api_key:
            Logger.warning(f"API key missing for model '{model}'. Please configure it in settings.")
            return None

        extra: Dict[str, Any] = {}
        for key in ("base_url", "organization", "project", "timeout", "max_retries"):
            value = config.get(key)
            if value is not None:
                extra[key] = value

        return ChatOpenAI(model=model, api_key=api_key, **extra)

    def _build_llm(self) -> Optional[BaseChatModel]:
        """Create a fresh LLM client for a single execution."""
        model_key = self._resolve_model_key()
        if not model_key:
            Logger.error("[BrowserUseTool] No model selected. Please select a model in TUI first.")
            return None

        llm_config = Config.get_provider_config(model_key)
        if not llm_config:
            Logger.error(f"[BrowserUseTool] Config not found for provider: {model_key}")
            return None

        return self._create_llm(llm_config)

    # ── execution ───────────────────────────────────────────────

    async def execute_async(self, task: str, max_steps: int = DEFAULT_MAX_STEPS) -> str:
        """Run the browser-use Agent asynchronously and return the final extracted text."""
        llm = self._build_llm()
        if not llm:
            resp = "Error: BrowserUseTool LLM is not configured. Please select a model in TUI."
            Logger.error(resp)
            return resp

        max_steps = max(max_steps, 1)
        
        # 只使用 Playwright 安装的 Chromium（干净、无扩展、版本可控）
        browser_path = _find_playwright_chromium()
        if not browser_path:
            resp = "Error: Playwright Chromium not installed. Run 'playwright install chromium' first."
            Logger.error(resp)
            return resp
        
        Logger.info(f"[BrowserUseTool] Using Playwright Chromium: {browser_path}")

        # 确保 browser-use 内部的 HTTP 请求（连接本机 CDP）不走系统代理
        # macOS 系统级 SOCKS 代理会导致 httpx/aiohttp 连 localhost 也走代理从而失败
        # _saved_no_proxy = os.environ.get('NO_PROXY')
        # os.environ['NO_PROXY'] = os.environ.get('NO_PROXY', '') + ',127.0.0.1,localhost'

        browser = Browser(
            headless=False,
            executable_path=browser_path,
        )

        try:
            agent = Agent(task=task, llm=llm, browser=browser)
            history: AgentHistoryList = await agent.run(max_steps=max_steps)

            final_output = history.final_result()
            if final_output:
                return str(final_output)

            errors = [e for e in history.errors() if e]
            if errors:
                resp = f"Task completed, but browser-use reported errors: {'; '.join(errors)}"
                Logger.error(resp)
                return resp

            return self._summarize_history(history)

        except Exception as exc:  # pragma: no cover
            resp = f"Error executing browser task: {exc}"
            Logger.error(resp)
            return resp
        finally:
            pass
            # # 恢复 NO_PROXY 环境变量
            # if _saved_no_proxy is None:
            #     os.environ.pop('NO_PROXY', None)
            # else:
            #     os.environ['NO_PROXY'] = _saved_no_proxy

    @schema_strict_validator
    def execute(self, task: str, max_steps: Optional[int] = None) -> str:
        """Synchronous wrapper around *execute_async*."""
        coro = self.execute_async(task, max_steps if max_steps is not None else DEFAULT_MAX_STEPS)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        if nest_asyncio is None:
            msg = (
                "BrowserUseTool requires nest_asyncio to run from an existing async loop. "
                "Install nest-asyncio or call execute_async directly."
            )
            Logger.error(msg)
            return msg

        global _nest_asyncio_applied
        if not _nest_asyncio_applied:
            nest_asyncio.apply()
            _nest_asyncio_applied = True

        return asyncio.get_event_loop().run_until_complete(coro)

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _summarize_history(history: AgentHistoryList) -> str:
        if not history.history:
            return "Task finished but produced no history."

        last_step = history.history[-1]
        last_result = last_step.result[-1] if last_step.result else None
        parts: list[str] = []

        if last_result:
            for label, value in (
                ("Extracted content", last_result.extracted_content),
                ("Long-term memory", last_result.long_term_memory),
                ("Error", last_result.error),
            ):
                if value:
                    parts.append(f"{label}: {value}")

        if parts:
            return " | ".join(parts)

        if last_step.model_output:
            actions: list[str] = []
            for action in last_step.model_output.action:
                data = action.model_dump(exclude_none=True, mode="json")
                if data:
                    actions.append(next(iter(data.keys())))
            if actions:
                return f"Last actions executed: {', '.join(actions)}."

        return "Task completed but browser-use did not produce textual output."
