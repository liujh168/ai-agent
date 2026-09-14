"""搜索类工具：web_search（优先博查 AI 搜索，降级 DuckDuckGo）"""
import os
import json
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


def web_search(query: str, max_results: int = 3) -> str:
    """
    调用博查 AI 搜索 API（优先），未配置 API key 时降级 DuckDuckGo。
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
            if status == 401:
                return f"博查 API Key 无效（401），请检查 BOCHA_API_KEY 环境变量。错误：{e}"
            return f"博查 API 出错（HTTP {status}）：{e}"
        except Exception as e:
            return f"博查搜索异常：{e}\n（未降级 DuckDuckGo，请检查网络连接）"

    # ---------- 路径 2：降级 DuckDuckGo ----------
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
        return out + "\n\n（提示：国内网络不稳定，配置 BOCHA_API_KEY 可切换博查 AI 搜索）"
    except ImportError:
        return "未安装 ddgs 库，请执行 `pip install ddgs`，或设置 BOCHA_API_KEY 走博查路径"
    except Exception as e:
        return f"DuckDuckGo 搜索异常：{e}\n（国内网络建议配置博查 API Key：set BOCHA_API_KEY=xxx）"
