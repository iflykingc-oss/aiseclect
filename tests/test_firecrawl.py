"""
Firecrawl 客户端单元测试（无需真实 API key）

覆盖：
- 占位 key 探测：未配置 / fc-xxx / placeholder / 短字符串都视为禁用
- is_available() 在无 key 时返回 False
- scrape_url() 在未配置 key 时返回 success=False + 明确错误
- scrape_url() 处理网络异常 / HTTP 错误 / 空 markdown
- _fetch_article()（content_enricher_node 内）在 Firecrawl 不可用时降级到 requests 路径

运行：pytest tests/test_firecrawl.py -v
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from tools import firecrawl
from graphs.nodes.content_enricher_node import _fetch_article


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个测试前清掉 FIRECRAWL_API_KEY，测试需要时再注入。"""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    yield


# ============ 占位 key 探测 ============

@pytest.mark.parametrize("bad_key", [
    "",
    "   ",
    "fc-xxx",
    "fc-xxxxxxxxxxxxxxxxxx-replace-with-real-key",
    "fc-placeholder-anything-here",
    "short",  # 太短
    "fc-abc",  # 格式对但太短
])
def test_placeholder_key_detected(bad_key):
    assert firecrawl._is_placeholder_key(bad_key) is True


@pytest.mark.parametrize("good_key", [
    "fc-real-key-with-enough-length-12345",
    "fc-abcdefghijklmnopqrstuvwxyz123456",
    "custom-firecrawl-instance-key-1234567890",  # 自部署允许非 fc- 开头
])
def test_real_key_accepted(good_key):
    # 至少 16 字符算合法
    if not good_key.startswith("fc-"):
        # 自部署格式要求 >=16 字符
        assert len(good_key) >= 16
    assert firecrawl._is_placeholder_key(good_key) is False


# ============ is_available ============

def test_is_available_false_without_key():
    assert firecrawl.is_available() is False


def test_is_available_false_with_placeholder(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-xxx")
    assert firecrawl.is_available() is False


def test_is_available_true_with_real_key(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")
    assert firecrawl.is_available() is True


# ============ scrape_url 错误处理 ============

def test_scrape_url_returns_disabled_when_no_key():
    result = firecrawl.scrape_url("https://example.com")
    assert result.success is False
    assert "not configured" in result.error.lower()


def test_scrape_url_returns_error_on_http_401(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("tools.firecrawl.requests.post", return_value=mock_resp):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is False
    assert "401" in result.error


def test_scrape_url_returns_error_on_network_failure(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    with patch("tools.firecrawl.requests.post", side_effect=requests.ConnectionError("boom")):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is False
    assert "network" in result.error.lower()


def test_scrape_url_returns_error_on_empty_markdown(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "data": {"markdown": "", "metadata": {}}}

    with patch("tools.firecrawl.requests.post", return_value=mock_resp):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is False
    assert "empty" in result.error.lower()


def test_scrape_url_returns_error_on_invalid_json(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("bad json")

    with patch("tools.firecrawl.requests.post", return_value=mock_resp):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is False
    assert "json" in result.error.lower()


def test_scrape_url_returns_error_on_api_success_false(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": False, "error": "page blocked"}

    with patch("tools.firecrawl.requests.post", return_value=mock_resp):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is False
    assert "blocked" in result.error


# ============ scrape_url 成功路径 ============

def test_scrape_url_success_returns_markdown(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "success": True,
        "data": {
            "markdown": "# Title\n\nBody content here.",
            "metadata": {"title": "Title", "description": "Desc"},
        },
    }

    with patch("tools.firecrawl.requests.post", return_value=mock_resp):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is True
    assert "Body content" in result.markdown
    assert result.metadata_title == "Title"


def test_scrape_url_truncates_huge_markdown(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    huge = "a" * 50000
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "success": True,
        "data": {"markdown": huge, "metadata": {}},
    }

    with patch("tools.firecrawl.requests.post", return_value=mock_resp):
        result = firecrawl.scrape_url("https://example.com")
    assert result.success is True
    assert len(result.markdown) <= 20000


# ============ _fetch_article 集成行为 ============

def test_fetch_article_falls_back_to_requests_when_no_key(monkeypatch):
    """无 FIRECRAWL_API_KEY 时，_fetch_article 应该走 requests 路径"""
    # 模拟 requests.get 返回成功 HTML
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.encoding = "utf-8"
    mock_resp.text = "<html><body><p>Hello world from fallback</p></body></html>"
    mock_resp.apparent_encoding = "utf-8"

    with patch("graphs.nodes.content_enricher_node.requests.get", return_value=mock_resp):
        result = _fetch_article("https://example.com/test")
    assert "Hello world from fallback" in result


def test_fetch_article_uses_firecrawl_when_available(monkeypatch):
    """Firecrawl 可用时，_fetch_article 应该走 Firecrawl 路径"""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    fc_result = firecrawl.FirecrawlResult(
        url="https://example.com",
        markdown="# Test\n\nClean markdown content from firecrawl.",
        success=True,
    )

    # requests.get 不应该被调用（Firecrawl 路径走 requests.post）
    with patch("tools.firecrawl.scrape_url", return_value=fc_result) as mock_scrape, \
         patch("graphs.nodes.content_enricher_node.requests.get") as mock_get:
        result = _fetch_article("https://example.com")
    mock_scrape.assert_called_once_with("https://example.com")
    mock_get.assert_not_called()
    assert "Clean markdown content from firecrawl" in result


def test_fetch_article_falls_back_when_firecrawl_fails(monkeypatch):
    """Firecrawl 调用失败（success=False）时，降级到 requests 路径"""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    fc_failed = firecrawl.FirecrawlResult(url="https://example.com", success=False, error="http 500")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.encoding = "utf-8"
    mock_resp.text = "<html><body><p>Fallback content after firecrawl fail</p></body></html>"

    with patch("tools.firecrawl.scrape_url", return_value=fc_failed), \
         patch("graphs.nodes.content_enricher_node.requests.get", return_value=mock_resp):
        result = _fetch_article("https://example.com")
    assert "Fallback content" in result


def test_fetch_article_falls_back_when_firecrawl_raises(monkeypatch):
    """Firecrawl 抛异常时，降级到 requests 路径"""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.encoding = "utf-8"
    mock_resp.text = "<html><body><p>Fallback after exception</p></body></html>"

    with patch("tools.firecrawl.scrape_url", side_effect=RuntimeError("boom")), \
         patch("graphs.nodes.content_enricher_node.requests.get", return_value=mock_resp):
        result = _fetch_article("https://example.com")
    assert "Fallback after exception" in result


def test_fetch_article_returns_empty_when_all_paths_fail(monkeypatch):
    """Firecrawl + requests 都失败时返回空字符串"""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-real-key-with-enough-length-12345")

    fc_failed = firecrawl.FirecrawlResult(url="https://example.com", success=False, error="http 500")

    with patch("tools.firecrawl.scrape_url", return_value=fc_failed), \
         patch("graphs.nodes.content_enricher_node.requests.get",
               side_effect=requests.ConnectionError("boom")):
        result = _fetch_article("https://example.com")
    assert result == ""