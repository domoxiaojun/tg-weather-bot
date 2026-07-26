import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Optional, TypeVar

import redis.asyncio as redis
from loguru import logger

from core.config import settings


T = TypeVar("T")


class CacheManager:
    """Async Redis cache with an in-memory TTL fallback."""

    def __init__(self):
        self.redis = None
        self._memory_cache: dict[str, tuple[Optional[float], str]] = {}
        self._redis_unavailable_until = 0.0
        self._redis_logged_ready = False
        self._last_memory_cleanup = 0.0
        self._memory_cache_max_items = 2048
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    def _cleanup_memory_cache(self):
        now = time.monotonic()
        if now - self._last_memory_cleanup < 60 and len(self._memory_cache) <= self._memory_cache_max_items:
            return

        self._last_memory_cleanup = now
        expired_keys = [
            key
            for key, (expires_at, _) in self._memory_cache.items()
            if expires_at is not None and expires_at <= now
        ]
        for key in expired_keys:
            self._memory_cache.pop(key, None)

        overflow = len(self._memory_cache) - self._memory_cache_max_items
        if overflow <= 0:
            return

        sorted_keys = sorted(
            self._memory_cache,
            key=lambda key: self._memory_cache[key][0] if self._memory_cache[key][0] is not None else float("inf"),
        )
        for key in sorted_keys[:overflow]:
            self._memory_cache.pop(key, None)

    async def _get_redis(self):
        if self.redis is not None:
            return self.redis

        now = time.monotonic()
        if now < self._redis_unavailable_until:
            return None

        try:
            self.redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self.redis.ping()
            if not self._redis_logged_ready:
                logger.info("Redis连接成功")
                self._redis_logged_ready = True
            return self.redis
        except Exception as e:
            logger.warning(f"Redis连接失败: {e}，将使用内存缓存")
            self.redis = None
            self._redis_unavailable_until = time.monotonic() + 30
            return None

    async def _mark_redis_failed(self, error: Exception):
        logger.error(f"Redis操作失败: {error}，临时切换到内存缓存")
        if self.redis is not None:
            try:
                await self.redis.aclose()
            except Exception:
                pass
        self.redis = None
        self._redis_unavailable_until = time.monotonic() + 30

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        try:
            redis_client = await self._get_redis()
            if redis_client:
                value = await redis_client.get(key)
                if value is not None:
                    return json.loads(value)
                return None
        except Exception as e:
            logger.error(f"缓存读取失败: {e}")
            if self.redis is not None:
                await self._mark_redis_failed(e)

        self._cleanup_memory_cache()
        cached = self._memory_cache.get(key)
        if not cached:
            return None

        expires_at, json_value = cached
        if expires_at is not None and expires_at <= time.monotonic():
            self._memory_cache.pop(key, None)
            return None
        return json.loads(json_value)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        设置缓存
        ttl: 过期时间（秒），None=永久
        """
        try:
            json_value = json.dumps(value, ensure_ascii=False)
        except Exception as e:
            logger.error(f"缓存序列化失败: {e}")
            return

        if ttl is not None and ttl <= 0:
            await self.delete(key)
            return

        try:
            redis_client = await self._get_redis()
            if redis_client:
                if ttl is not None:
                    await redis_client.setex(key, ttl, json_value)
                else:
                    await redis_client.set(key, json_value)
                return
        except Exception as e:
            logger.error(f"缓存写入失败: {e}")
            if self.redis is not None:
                await self._mark_redis_failed(e)

        expires_at = time.monotonic() + ttl if ttl is not None else None
        self._memory_cache[key] = (expires_at, json_value)
        self._cleanup_memory_cache()

    async def delete(self, key: str):
        """删除缓存"""
        try:
            redis_client = await self._get_redis()
            if redis_client:
                await redis_client.delete(key)
        except Exception as e:
            logger.error(f"缓存删除失败: {e}")
            if self.redis is not None:
                await self._mark_redis_failed(e)
        finally:
            self._memory_cache.pop(key, None)

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl: Optional[int] | Callable[[T], Optional[int]] = None,
        *,
        force_refresh: bool = False,
    ) -> Optional[T]:
        """Return a cached value or share one in-flight async factory call.

        Only non-``None`` results are cached. Waiting callers are shielded from
        cancelling the shared task, so one Telegram request timing out cannot
        abort a provider request that other callers are awaiting.
        """
        if not force_refresh:
            cached = await self.get(key)
            if cached is not None:
                return cached

        existing = self._inflight.get(key)
        if existing is not None:
            logger.debug(f"Cache single-flight join: {key}")
            return await asyncio.shield(existing)

        async def load_value() -> Optional[T]:
            # A second read closes the small race with another process writing
            # Redis between the caller's initial lookup and task creation.
            if not force_refresh:
                cached_value = await self.get(key)
                if cached_value is not None:
                    return cached_value

            value = await factory()
            if value is not None:
                resolved_ttl = ttl(value) if callable(ttl) else ttl
                await self.set(key, value, ttl=resolved_ttl)
            return value

        task: asyncio.Task[Optional[T]] = asyncio.create_task(load_value())
        self._inflight[key] = task

        def discard_finished(done_task: asyncio.Task[Any]) -> None:
            if self._inflight.get(key) is done_task:
                self._inflight.pop(key, None)

        task.add_done_callback(discard_finished)
        return await asyncio.shield(task)

    async def cancel_inflight(self):
        """Cancel and drain outstanding loaders during application shutdown."""
        tasks = list(dict.fromkeys(self._inflight.values()))
        self._inflight.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        """Close Redis connection when the application shuts down."""
        await self.cancel_inflight()
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None


# 全局单例
cache = CacheManager()
