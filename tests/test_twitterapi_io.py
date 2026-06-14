"""Tests for TwitterAPIIOScraper."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from src.models import TwitterConfig
from src.scrapers.twitterapi_io import TwitterAPIIOScraper


def _make_config(**kwargs) -> TwitterConfig:
    defaults = dict(
        enabled=True,
        mode="twitterapi_io",
        users=["karpathy"],
        fetch_limit=3,
        twitterapi_key_env="TWITTERAPI_IO_KEY",
        twitterapi_base_url="https://api.twitterapi.io",
    )
    defaults.update(kwargs)
    return TwitterConfig(**defaults)


def _tweet(
    tweet_id: str = "123456",
    screen_name: str = "karpathy",
    text: str = "Hello from TwitterAPI.io",
    created_at: str = None,
    **extra,
) -> dict:
    if created_at is None:
        created_at = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S +0000 %Y")
    return {
        "id": tweet_id,
        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
        "text": text,
        "createdAt": created_at,
        "retweetCount": 2,
        "replyCount": 1,
        "likeCount": 10,
        "quoteCount": 3,
        "viewCount": 1000,
        "bookmarkCount": 4,
        "isReply": False,
        "conversationId": tweet_id,
        "lang": "en",
        "author": {
            "id": "42",
            "userName": screen_name,
            "name": screen_name.capitalize(),
        },
        **extra,
    }


def _response(tweets: list[dict], **extra) -> dict:
    data = {
        "status": "success",
        "tweets": tweets,
        "has_next_page": False,
        "next_cursor": None,
    }
    data.update(extra)
    return data


def test_missing_key_returns_empty(monkeypatch):
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(
        TwitterAPIIOScraper(_make_config(), client).fetch(datetime.now(timezone.utc))
    )
    asyncio.run(client.aclose())

    assert result == []


def test_successful_fetch_returns_items(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    seen_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert request.url.path == "/twitter/user/last_tweets"
        assert request.headers["X-API-Key"] == "test_key"
        assert "test_key" not in str(request.url)
        assert request.url.params["userName"] == "karpathy"
        assert request.url.params["includeReplies"] == "false"
        return httpx.Response(200, json=_response([_tweet("1"), _tweet("2")]))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(TwitterAPIIOScraper(_make_config(), client).fetch(since))
    asyncio.run(client.aclose())

    assert len(seen_requests) == 1
    assert len(result) == 2
    assert result[0].source_type.value == "twitter"
    assert result[0].id == "twitter:tweet:1"
    assert str(result[0].url) == "https://x.com/karpathy/status/1"
    assert result[0].author == "Karpathy"
    assert result[0].metadata["provider"] == "twitterapi_io"
    assert result[0].metadata["favorite_count"] == 10
    assert result[0].metadata["retweet_count"] == 2
    assert result[0].metadata["reply_count"] == 1


def test_auto_source_uses_list_timeline_when_list_id_env_is_set(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")
    monkeypatch.setenv("TWITTERAPI_LIST_ID", "12345")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    seen_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert request.url.path == "/twitter/list/tweets_timeline"
        assert request.headers["X-API-Key"] == "test_key"
        assert request.url.params["listId"] == "12345"
        assert "userName" not in request.url.params
        return httpx.Response(200, json=_response([_tweet("1")]))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(TwitterAPIIOScraper(_make_config(), client).fetch(since))
    asyncio.run(client.aclose())

    assert len(seen_requests) == 1
    assert len(result) == 1
    assert result[0].metadata["provider"] == "twitterapi_io"


def test_list_source_without_list_id_returns_empty(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")
    monkeypatch.delenv("TWITTERAPI_LIST_ID", raising=False)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(
        TwitterAPIIOScraper(
            _make_config(twitterapi_source="list"),
            client,
        ).fetch(datetime.now(timezone.utc))
    )
    asyncio.run(client.aclose())

    assert result == []


def test_old_tweets_filtered(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    old_created_at = (since - timedelta(minutes=5)).strftime(
        "%a %b %d %H:%M:%S +0000 %Y"
    )

    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json=_response([_tweet(created_at=old_created_at)]))
    )
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(TwitterAPIIOScraper(_make_config(), client).fetch(since))
    asyncio.run(client.aclose())

    assert result == []


def test_error_response_returns_empty(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")

    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "status": "error",
                "message": "bad request",
                "tweets": [],
            },
        )
    )
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(
        TwitterAPIIOScraper(_make_config(), client).fetch(datetime.now(timezone.utc))
    )
    asyncio.run(client.aclose())

    assert result == []


def test_429_is_retried_with_retry_after(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")

    async def noop_sleep(_):
        return None

    monkeypatch.setattr("src.scrapers.twitterapi_io.asyncio.sleep", noop_sleep)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=_response([_tweet("1")]))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(
        TwitterAPIIOScraper(_make_config(twitterapi_429_retries=1), client).fetch(since)
    )
    asyncio.run(client.aclose())

    assert calls == 2
    assert len(result) == 1


def test_max_pages_per_user_prevents_pagination(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test_key")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    seen_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json=_response(
                [_tweet("1")],
                has_next_page=True,
                next_cursor="next",
            ),
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    result = asyncio.run(
        TwitterAPIIOScraper(
            _make_config(fetch_limit=20, twitterapi_max_pages_per_user=1),
            client,
        ).fetch(since)
    )
    asyncio.run(client.aclose())

    assert len(seen_requests) == 1
    assert len(result) == 1
