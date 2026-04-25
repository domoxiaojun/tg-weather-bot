import json
import time
from typing import Any, Optional

import redis.asyncio as redis
from loguru import logger

from core.config import settings


class CacheManager:
    """Async Redis cache with an in-memory TTL fallback."""

    def __init__(self):
        self.redis = None
        self._memory_cache: dict[str, tuple[Optional[float], str]] = {}
        self._redis_unavailable_until = 0.0
        self._redis_logged_ready = False

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

    async def close(self):
        """Close Redis connection when the application shuts down."""
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None


# 全局单例
cache = CacheManager()
