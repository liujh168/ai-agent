"""搜索类工具：web_search（优先博查 AI 搜索，降级 DuckDuckGo）"""

import os

import requests


def _format_results(items, max_results):
    """统一格式化搜索结果"""
    if not items:
        return "未找到相关结果"
    lines = []
    for i, item in enumerate(items[:max_results], 1):
        url = item.get("url", "")
        summary = item.get("summary") or item.get("snippet", "")
        site = item.get("siteName", "")
        date = item.get("datePublished", "")
        lines.append(f"[{i}] {item.get('name', '无标题')}  [{site}, {date}]\n    {url}\n    {summary[:300]}")
    return "\n".join(lines)


def _duckduckgo_search(query: str, max_results: int) -> str:
    """降级路径：使用 DuckDuckGo 搜索（无需 API key）。"""
    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "DuckDuckGo 返回空结果"
        items = [
            {"name": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in results
        ]
        out = _format_results(items, max_results)
        return out + "\n\n（提示：已降级 DuckDuckGo 搜索，配置 BOCHA_API_KEY 可切换博查 AI 搜索）"
    except ImportError:
        return "未安装 ddgs 库，请执行 `pip install ddgs`，或设置 BOCHA_API_KEY 走博查路径"
    except Exception as e:
        return f"DuckDuckGo 搜索异常：{e}\n（国内网络建议配置博查 API Key：set BOCHA_API_KEY=xxx）"


def web_search(query: str, max_results: int = 3) -> str:
    """
    调用博查 AI 搜索 API（优先），未配置 API key 或博查报错时降级 DuckDuckGo。
    国内访问稳定。
    """
    bocha_key = os.environ.get("BOCHA_API_KEY", "").strip()

    # ---------- 路径 1：博查 AI 搜索 ----------
    if bocha_key:
        try:
            resp = requests.post(
                "https://api.bochaai.com/v1/web-search",
                headers={
                    "Authorization": f"Bearer {bocha_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "summary": True, "count": max_results + 2, "freshness": "noLimit"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("webPages", {}).get("value", [])
            if items:
                return _format_results(items, max_results)
            return "博查返回空结果，请换关键词再试"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            # 博查不可用（401/403 等）时降级到 DuckDuckGo，避免直接失败
            return f"博查 API 出错（HTTP {status}），已降级到 DuckDuckGo：\n" + _duckduckgo_search(
                query, max_results
            )
        except Exception as e:
            return f"博查搜索异常：{e}\n（已尝试降级 DuckDuckGo）\n" + _duckduckgo_search(
                query, max_results
            )

    # ---------- 路径 2：降级 DuckDuckGo ----------
    return _duckduckgo_search(query, max_results)
