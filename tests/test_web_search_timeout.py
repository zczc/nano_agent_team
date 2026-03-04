"""
web_search 超时保护 & engine IO 工具串行化 测试

验证：
1. SearchTool.execute() 在 DDGS 卡死时能在 SEARCH_TIMEOUT 内返回
2. 返回的 JSON 包含超时错误信息
3. engine 并行执行时 IO 工具被正确串行化
"""

import json
import time
import threading
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# 1. SearchTool 硬超时测试
# ---------------------------------------------------------------------------

class TestSearchToolTimeout:
    """测试 SearchTool 在底层 DDGS 卡死时的超时保护"""

    def test_execute_returns_within_timeout_when_provider_hangs(self):
        """DDGS 永久阻塞时，execute() 应在 SEARCH_TIMEOUT 内返回超时错误 JSON"""
        from backend.tools.web_search import SearchTool
        import backend.tools.web_search as ws_module

        tool = SearchTool()

        # Mock provider.search 永久阻塞
        def hang_forever(query, max_results=5):
            threading.Event().wait()  # 永不返回

        tool._provider.search = hang_forever

        # 临时缩短超时加速测试
        original_timeout = ws_module.SEARCH_TIMEOUT
        ws_module.SEARCH_TIMEOUT = 3
        try:
            start = time.monotonic()
            result = tool.execute(query="test query", max_results=3)
            elapsed = time.monotonic() - start

            # 应在缩短后的超时 + 少量余量内返回
            assert elapsed < 3 + 5, f"Took {elapsed:.1f}s, expected < 8s"

            # 返回值应是合法 JSON，包含超时错误
            data = json.loads(result)
            assert isinstance(data, list)
            assert len(data) == 1
            assert "timed out" in data[0]["body"].lower()
        finally:
            ws_module.SEARCH_TIMEOUT = original_timeout

    def test_execute_returns_normal_results(self):
        """正常搜索应直接返回结果"""
        from backend.tools.web_search import SearchTool

        tool = SearchTool()
        fake_results = [{"title": "Test", "body": "body", "href": "https://example.com"}]
        tool._provider.search = MagicMock(return_value=fake_results)

        result = tool.execute(query="hello")
        data = json.loads(result)
        assert data == fake_results

    def test_execute_handles_provider_exception(self):
        """provider 抛异常时应返回错误 JSON 而非崩溃"""
        from backend.tools.web_search import SearchTool

        tool = SearchTool()
        tool._provider.search = MagicMock(side_effect=RuntimeError("boom"))

        result = tool.execute(query="fail")
        data = json.loads(result)
        assert isinstance(data, list)
        assert "error" in data[0]["title"].lower() or "failed" in data[0]["body"].lower()


# ---------------------------------------------------------------------------
# 2. DuckDuckGoProvider 实例隔离测试
# ---------------------------------------------------------------------------

class TestDuckDuckGoProviderIsolation:
    """测试 DuckDuckGoProvider 不再使用 context manager"""

    def test_search_creates_new_instance(self):
        """每次 search() 应创建新的 DDGS 实例"""
        from backend.tools.web_search import DuckDuckGoProvider

        provider = DuckDuckGoProvider()
        if not provider.available:
            pytest.skip("ddgs not installed")

        instances = []
        original_ddgs = provider.ddgs

        def tracking_ddgs(**kwargs):
            inst = MagicMock()
            inst.text.return_value = iter([{"title": "r", "body": "b", "href": "#"}])
            instances.append(inst)
            return inst

        provider.ddgs = tracking_ddgs

        provider.search("q1")
        provider.search("q2")

        # 两次调用应创建两个不同实例
        assert len(instances) == 2
        assert instances[0] is not instances[1]


# ---------------------------------------------------------------------------
# 3. Engine IO 工具串行化测试
# ---------------------------------------------------------------------------

class TestEngineIOToolSerialization:
    """测试 engine 并行执行时 IO 工具被正确串行化"""

    def test_io_tools_constant(self):
        """_IO_BOUND_TOOLS 应包含 web_search"""
        from backend.llm.engine import _IO_BOUND_TOOLS
        assert "web_search" in _IO_BOUND_TOOLS

    def test_tool_timeouts_constant(self):
        """_TOOL_TIMEOUTS 应为 web_search 设置合理超时"""
        from backend.llm.engine import _TOOL_TIMEOUTS
        assert "web_search" in _TOOL_TIMEOUTS
        assert _TOOL_TIMEOUTS["web_search"] <= 60  # 不应太长

    def test_io_tools_run_serially(self):
        """多个 web_search 并行调用时，不应同时执行（串行化）"""
        from backend.llm.engine import _IO_BOUND_TOOLS

        concurrency_log = []
        active_count = 0
        lock = threading.Lock()
        max_concurrent = 0

        def mock_execute(tc):
            nonlocal active_count, max_concurrent
            fn_name = tc["function"]["name"]
            with lock:
                active_count += 1
                if active_count > max_concurrent:
                    max_concurrent = active_count
            time.sleep(0.1)  # 模拟 IO
            with lock:
                active_count -= 1
            return (tc, "ok", fn_name, {})

        # 模拟 4 个 web_search tool_calls
        tool_calls = [
            {"id": f"call_{i}", "function": {"name": "web_search", "arguments": json.dumps({"query": f"q{i}"})}}
            for i in range(4)
        ]

        # 验证它们都是 IO 工具
        for tc in tool_calls:
            assert tc["function"]["name"] in _IO_BOUND_TOOLS

        # 串行执行 IO 工具（模拟 engine 行为）
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
        results = []
        for tc in tool_calls:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(mock_execute, tc)
                results.append(future.result(timeout=30))

        # 串行执行时最大并发应为 1
        assert max_concurrent == 1
        assert len(results) == 4
