import redis
from typing import Optional, Any
import json
from loguru import logger
from core.config import settings

class CacheManager:
    """Redis缓存管理器"""
    
    def __init__(self):
        try:
            self.redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5
            )
            self.redis.ping()
            logger.info("Redis连接成功")
        except Exception as e:
            logger.warning(f"Redis连接失败: {e}，将使用内存缓存")
            self.redis = None
            self._memory_cache = {}
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        try:
            if self.redis:
                value = self.redis.get(key)
                if value:
                    return json.loads(value)
            else:
                return self._memory_cache.get(key)
        except Exception as e:
            logger.error(f"缓存读取失败: {e}")
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        设置缓存
        ttl: 过期时间（秒），None=永久
        """
        try:
            json_value = json.dumps(value, ensure_ascii=False)
            if self.redis:
                if ttl:
                    self.redis.setex(key, ttl, json_value)
                else:
                    self.redis.set(key, json_value)
            else:
                self._memory_cache[key] = value
        except Exception as e:
            logger.error(f"缓存写入失败: {e}")
    
    def delete(self, key: str):
        """删除缓存"""
        try:
            if self.redis:
                self.redis.delete(key)
            else:
                self._memory_cache.pop(key, None)
        except Exception as e:
            logger.error(f"缓存删除失败: {e}")

# 全局单例
cache = CacheManager()
