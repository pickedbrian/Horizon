"""Tests for enrichment web search providers."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from src.ai.enricher import ContentEnricher


class _DummyAIClient:
    def __init__(self, **config):
        self.config = SimpleNamespace(**config)


def test_serper_search_uses_header_and_parses_organic(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test_key")
    seen_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        assert request.url == "https://google.serper.dev/search"
        assert request.headers["X-API-KEY"] == "test_key"
        assert "test_key" not in str(request.url)
        assert payload == {"q": "apple inc", "num": 2}
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Apple",
                        "link": "https://www.apple.com/",
                        "snippet": "Apple official site",
                    },
                    {
                        "title": "Apple Newsroom",
                        "link": "https://www.apple.com/newsroom/",
                        "snippet": "Latest news",
                    },
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    enricher = ContentEnricher(
        _DummyAIClient(search_provider="serper", search_max_results=2),
        search_client=client,
    )

    result = asyncio.run(enricher._web_search("apple inc"))
    asyncio.run(client.aclose())

    assert len(seen_requests) == 1
    assert result == [
        {
            "title": "Apple",
            "url": "https://www.apple.com/",
            "body": "Apple official site",
        },
        {
            "title": "Apple Newsroom",
            "url": "https://www.apple.com/newsroom/",
            "body": "Latest news",
        },
    ]


def test_serper_search_supports_news_shape(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test_key")

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "news": [
                    {
                        "title": "Apple headline",
                        "link": "https://example.com/apple",
                        "snippet": "News snippet",
                    }
                ]
            },
        )
    )
    client = httpx.AsyncClient(transport=transport)
    enricher = ContentEnricher(
        _DummyAIClient(search_provider="serper", serper_endpoint="news"),
        search_client=client,
    )

    result = asyncio.run(enricher._web_search("apple inc"))
    asyncio.run(client.aclose())

    assert result == [
        {
            "title": "Apple headline",
            "url": "https://example.com/apple",
            "body": "News snippet",
        }
    ]


def test_serper_missing_key_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    enricher = ContentEnricher(_DummyAIClient(search_provider="serper"))

    async def fake_duckduckgo_search(query: str, max_results: int = 3):
        return [{"title": query, "url": "https://example.com", "body": str(max_results)}]

    enricher._duckduckgo_search = fake_duckduckgo_search

    result = asyncio.run(enricher._web_search("apple inc", max_results=2))

    assert result == [
        {"title": "apple inc", "url": "https://example.com", "body": "2"}
    ]
