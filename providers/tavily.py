"""Tavily web search provider."""

from __future__ import annotations

from typing import Any

from config import Settings, get_settings

try:
    from langfuse import observe as _lf_observe

    def _tool_observe(fn: Any) -> Any:
        return _lf_observe(fn, as_type="tool", capture_input=True, capture_output=True)
except ImportError:

    def _tool_observe(fn: Any) -> Any:  # type: ignore[misc]
        return fn


DEFAULT_TAVILY_MAX_RESULTS = 5
_SNIPPET_MAX_CHARS = 800


class ToolExecutionError(RuntimeError):
    pass


@_tool_observe
def tavily_search(
    query: str,
    max_results: int = DEFAULT_TAVILY_MAX_RESULTS,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    api_key = settings.tavily_api_key.strip()
    if not api_key:
        raise ToolExecutionError("Tavily search is not configured. Set TAVILY_API_KEY in .env.")

    try:
        from tavily import TavilyClient
    except ImportError as exc:
        raise ToolExecutionError("The 'tavily-python' package is required for Tavily web search.") from exc

    clamped = max(1, min(int(max_results), DEFAULT_TAVILY_MAX_RESULTS))
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=clamped,
            include_answer=True,
        )
    except Exception as exc:
        raise ToolExecutionError(f"Tavily search failed: {exc}") from exc

    output: list[str] = []
    answer = str(response.get("answer", "")).strip() if isinstance(response, dict) else ""
    if answer:
        output.append(f"SUMMARY: {answer}")

    results = response.get("results", []) if isinstance(response, dict) else []
    for i, result in enumerate(results[:clamped], start=1):
        if not isinstance(result, dict):
            continue
        title = str(result.get("title", "Untitled")).strip() or "Untitled"
        snippet = str(result.get("content", "")).strip().replace("\n", " ")
        url = str(result.get("url", "")).strip()
        truncated = snippet[:_SNIPPET_MAX_CHARS] + ("..." if len(snippet) > _SNIPPET_MAX_CHARS else "")
        # URL on its own line so the model can copy it verbatim into data_sources
        output.append(f"[Result {i}] {title}\nURL: {url}\n{truncated}")

    return "\n\n---\n\n".join(output) if output else "No Tavily search results found."


def get_tavily_search_tool_definition(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "name": "tavily_search",
        "description": (
            "Search the web for information about a company, its financials, news, and analyst views. "
            f"Use at most {settings.analyst_max_searches} searches per stock analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query e.g. 'KPITTECH Q3 FY26 quarterly results revenue profit'",
                },
                "max_results": {
                    "type": "integer",
                    "default": DEFAULT_TAVILY_MAX_RESULTS,
                },
            },
            "required": ["query"],
        },
    }
