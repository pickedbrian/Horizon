"""Twitter scraper using TwitterAPI.io."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from html import unescape
from typing import Any, List, Optional

from dateutil.parser import isoparse
import httpx

from .base import BaseScraper
from ..models import ContentItem, SourceType, TwitterConfig

logger = logging.getLogger(__name__)


class TwitterAPIIOScraper(BaseScraper):
    """Fetch tweets via the TwitterAPI.io REST API."""

    def __init__(self, config: TwitterConfig, http_client: httpx.AsyncClient):
        super().__init__(config, http_client)
        self.config = config

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.config.enabled:
            return []

        api_key = os.environ.get(self.config.twitterapi_key_env)
        if not api_key:
            logger.warning(
                "TwitterAPI.io key not found in env var "
                f"'{self.config.twitterapi_key_env}'. Skipping Twitter."
            )
            return []

        list_id = self._configured_list_id()
        source = self._select_source(list_id)
        if source == "list":
            if not list_id:
                logger.warning(
                    "TwitterAPI.io source is 'list' but no list id is configured. "
                    f"Set {self.config.twitterapi_list_id_env} or twitterapi_list_id."
                )
                return []
            logger.info(f"Fetching Twitter (TwitterAPI.io list) for list: {list_id}")
            items = await self._fetch_list(api_key, list_id, since)
            return self._sort_and_limit(items)

        users = [u.strip().lstrip("@") for u in self.config.users if u.strip()]
        if not users:
            logger.debug("No Twitter users configured, skipping.")
            return []

        logger.info(f"Fetching Twitter (TwitterAPI.io users) for users: {users}")

        items: List[ContentItem] = []
        request_interval = max(self.config.twitterapi_request_interval_sec, 0.0)
        for index, user in enumerate(users):
            if index > 0 and request_interval > 0:
                await asyncio.sleep(request_interval)
            items.extend(await self._fetch_user(api_key, user, since))

        return self._sort_and_limit(items)

    def _sort_and_limit(self, items: List[ContentItem]) -> List[ContentItem]:
        items.sort(key=lambda item: item.published_at, reverse=True)
        if self.config.fetch_limit > 0:
            items = items[: self.config.fetch_limit]

        logger.info(f"Fetched {len(items)} tweets via TwitterAPI.io.")
        return items

    def _configured_list_id(self) -> Optional[str]:
        direct = (self.config.twitterapi_list_id or "").strip()
        if direct:
            return direct
        env_name = (self.config.twitterapi_list_id_env or "").strip()
        if not env_name:
            return None
        value = os.environ.get(env_name)
        return value.strip() if value else None

    def _select_source(self, list_id: Optional[str]) -> str:
        source = (self.config.twitterapi_source or "auto").strip().lower()
        if source == "auto":
            return "list" if list_id else "users"
        if source in ("user", "users", "last_tweets"):
            return "users"
        if source in ("list", "list_timeline", "timeline"):
            return "list"

        logger.warning(
            f"Unknown TwitterAPI.io source '{self.config.twitterapi_source}', "
            "falling back to users."
        )
        return "users"

    async def _fetch_list(
        self, api_key: str, list_id: str, since: datetime
    ) -> List[ContentItem]:
        url = f"{self.config.twitterapi_base_url.rstrip('/')}/twitter/list/tweets_timeline"
        headers = {"X-API-Key": api_key}
        params: dict[str, Any] = {
            "listId": list_id,
        }

        items: List[ContentItem] = []
        cursor: Optional[str] = None
        reached_old_tweet = False
        pages_fetched = 0
        max_pages = max(self.config.twitterapi_list_max_pages, 1)
        raw_count = 0
        filtered_old_count = 0
        parse_drop_count = 0
        newest_at: Optional[datetime] = None
        oldest_at: Optional[datetime] = None

        while pages_fetched < max_pages:
            if cursor:
                params["cursor"] = cursor
            else:
                params.pop("cursor", None)

            try:
                resp = await self._get_with_rate_limit_retry(
                    url, params=params, headers=headers
                )
                data = resp.json()
            except Exception as exc:
                logger.warning(f"TwitterAPI.io list fetch failed for {list_id}: {exc}")
                return items

            pages_fetched += 1

            if data.get("status") == "error":
                logger.warning(
                    "TwitterAPI.io returned an error for list %s: %s",
                    list_id,
                    data.get("message") or data.get("msg"),
                )
                return items

            tweets = data.get("tweets") or []
            if not isinstance(tweets, list):
                logger.warning(f"TwitterAPI.io returned invalid tweets for list {list_id}.")
                return items

            page_items = []
            for raw in tweets:
                raw_count += 1
                published_at = self._parse_published_at(raw.get("createdAt"))
                if published_at:
                    newest_at = (
                        published_at
                        if newest_at is None or published_at > newest_at
                        else newest_at
                    )
                    oldest_at = (
                        published_at
                        if oldest_at is None or published_at < oldest_at
                        else oldest_at
                    )
                    if published_at < since:
                        filtered_old_count += 1
                parsed = self._parse_item(raw, since, published_at=published_at)
                if parsed:
                    page_items.append(parsed)
                    continue

                if published_at and published_at < since:
                    reached_old_tweet = True
                else:
                    parse_drop_count += 1

            items.extend(page_items)

            if self.config.fetch_limit > 0 and len(items) >= self.config.fetch_limit:
                break
            if reached_old_tweet or not data.get("has_next_page"):
                break

            cursor = data.get("next_cursor")
            if not cursor:
                break

        if raw_count or pages_fetched:
            message = (
                f"TwitterAPI.io list {list_id}: pages={pages_fetched} raw={raw_count} "
                f"kept={len(items)} old={filtered_old_count} dropped={parse_drop_count} "
                f"newest={newest_at.isoformat() if newest_at else 'n/a'} "
                f"oldest={oldest_at.isoformat() if oldest_at else 'n/a'}"
            )
            if not items:
                logger.warning(message)
            else:
                logger.info(message)

        return items

    async def _fetch_user(
        self, api_key: str, user: str, since: datetime
    ) -> List[ContentItem]:
        url = f"{self.config.twitterapi_base_url.rstrip('/')}/twitter/user/last_tweets"
        headers = {"X-API-Key": api_key}
        params: dict[str, Any] = {
            "userName": user,
            "includeReplies": str(self.config.twitterapi_include_replies).lower(),
        }

        items: List[ContentItem] = []
        cursor: Optional[str] = None
        reached_old_tweet = False
        pages_fetched = 0
        max_pages = max(self.config.twitterapi_max_pages_per_user, 1)
        raw_count = 0
        filtered_old_count = 0
        parse_drop_count = 0
        newest_at: Optional[datetime] = None
        oldest_at: Optional[datetime] = None

        while pages_fetched < max_pages:
            if cursor:
                params["cursor"] = cursor
            else:
                params.pop("cursor", None)

            try:
                resp = await self._get_with_rate_limit_retry(
                    url, params=params, headers=headers
                )
                data = resp.json()
            except Exception as exc:
                logger.warning(f"TwitterAPI.io fetch failed for @{user}: {exc}")
                return items

            pages_fetched += 1

            if data.get("status") == "error":
                logger.warning(
                    f"TwitterAPI.io returned an error for @{user}: {data.get('message')}"
                )
                return items

            tweets = data.get("tweets") or []
            if not isinstance(tweets, list):
                logger.warning(f"TwitterAPI.io returned invalid tweets for @{user}.")
                return items

            page_items = []
            for raw in tweets:
                raw_count += 1
                published_at = self._parse_published_at(raw.get("createdAt"))
                if published_at:
                    newest_at = (
                        published_at
                        if newest_at is None or published_at > newest_at
                        else newest_at
                    )
                    oldest_at = (
                        published_at
                        if oldest_at is None or published_at < oldest_at
                        else oldest_at
                    )
                    if published_at < since:
                        filtered_old_count += 1
                parsed = self._parse_item(
                    raw,
                    since,
                    fallback_screen_name=user,
                    published_at=published_at,
                )
                if parsed:
                    page_items.append(parsed)
                    continue

                if published_at and published_at < since:
                    reached_old_tweet = True
                else:
                    parse_drop_count += 1

            items.extend(page_items)

            if self.config.fetch_limit > 0 and len(items) >= self.config.fetch_limit:
                break
            if reached_old_tweet or not data.get("has_next_page"):
                break

            cursor = data.get("next_cursor")
            if not cursor:
                break

        if raw_count or pages_fetched:
            message = (
                f"TwitterAPI.io @{user}: pages={pages_fetched} raw={raw_count} "
                f"kept={len(items)} old={filtered_old_count} dropped={parse_drop_count} "
                f"newest={newest_at.isoformat() if newest_at else 'n/a'} "
                f"oldest={oldest_at.isoformat() if oldest_at else 'n/a'}"
            )
            if not items:
                logger.warning(message)
            else:
                logger.info(message)

        return items

    async def _get_with_rate_limit_retry(
        self, url: str, params: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        """GET with conservative retry handling for TwitterAPI.io 429 responses."""
        retries = max(self.config.twitterapi_429_retries, 0)
        for attempt in range(retries + 1):
            resp = await self.client.get(
                url, params=params, headers=headers, timeout=30.0
            )
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp

            if attempt >= retries:
                resp.raise_for_status()

            retry_after = self._retry_after_seconds(resp)
            logger.warning(
                "TwitterAPI.io rate limited request; "
                f"retrying in {retry_after:.1f}s ({attempt + 1}/{retries})."
            )
            await asyncio.sleep(retry_after)

        raise RuntimeError("unreachable")

    @staticmethod
    def _retry_after_seconds(resp: httpx.Response) -> float:
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return max(float(raw), 1.0)
            except ValueError:
                pass
        return 10.0

    def _parse_item(
        self,
        item: dict,
        since: datetime,
        fallback_screen_name: str = "unknown",
        published_at: Optional[datetime] = None,
    ) -> Optional[ContentItem]:
        try:
            if published_at is None:
                published_at = self._parse_published_at(item.get("createdAt"))
            if not published_at or published_at < since:
                return None

            tweet_id = str(item.get("id") or "")
            if not tweet_id:
                return None

            author_data = item.get("author") or {}
            screen_name = (
                author_data.get("userName")
                or author_data.get("username")
                or item.get("userName")
                or fallback_screen_name
                or "unknown"
            )
            screen_name = str(screen_name).lstrip("@")
            author = author_data.get("name") or screen_name

            text = unescape((item.get("text") or "").strip())
            if not text:
                return None

            url = item.get("url") or f"https://x.com/{screen_name}/status/{tweet_id}"

            title_body = text[:50].replace("\n", " ").strip()
            if len(text) > 50:
                title_body += "..."

            conversation_id = str(item.get("conversationId") or tweet_id)

            return ContentItem(
                id=self._generate_id(SourceType.TWITTER.value, "tweet", tweet_id),
                source_type=SourceType.TWITTER,
                title=f"@{screen_name}: {title_body}",
                url=url,
                content=text,
                author=author,
                published_at=published_at,
                metadata={
                    "tweet_id": tweet_id,
                    "conversation_id": conversation_id,
                    "favorite_count": item.get("likeCount", 0),
                    "retweet_count": item.get("retweetCount", 0),
                    "reply_count": item.get("replyCount", 0),
                    "quote_count": item.get("quoteCount", 0),
                    "view_count": item.get("viewCount"),
                    "bookmark_count": item.get("bookmarkCount"),
                    "is_reply": item.get("isReply", False),
                    "in_reply_to_status_id": item.get("inReplyToId"),
                    "in_reply_to_screen_name": item.get("inReplyToUsername"),
                    "lang": item.get("lang"),
                    "author_username": screen_name,
                    "author_id": author_data.get("id"),
                    "provider": "twitterapi_io",
                },
            )
        except Exception as exc:
            logger.debug(f"Failed to parse TwitterAPI.io tweet: {exc}")
            return None

    @staticmethod
    def _parse_published_at(value: Any) -> Optional[datetime]:
        if not value:
            return None

        try:
            published_at = datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            try:
                published_at = isoparse(str(value))
            except (TypeError, ValueError):
                return None

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        return published_at
