"""Small, dependency-free internal HTTP API for another bot to call.

This intentionally uses :func:`asyncio.start_server` instead of adding a web
framework to the Telegram bot image.  The API shares the bot's event loop and
services, so provider/LLM caches and shutdown lifecycle remain consistent.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
from urllib.parse import parse_qs, quote, urlsplit

from loguru import logger

from core.config import settings
from services.fusion import WeatherFusionService
from services.llm import LLMService
from services.weather_card import render_weather_card
from utils.formatter import format_weather_response
from utils.rich_formatter import build_weather_blocks


MAX_HEADERS = 16 * 1024
CONCURRENCY_ACQUIRE_TIMEOUT = 0.05
GRACEFUL_SHUTDOWN_TIMEOUT = 5.0


class ExternalWeatherApi:
    """Token-protected HTTP surface for a separate chat-agent bot."""

    def __init__(self, weather_service: WeatherFusionService, llm_service: LLMService):
        self.weather_service = weather_service
        self.llm_service = llm_service
        self._server: asyncio.AbstractServer | None = None
        self._semaphore = asyncio.Semaphore(settings.weather_api_max_concurrency)
        self._active_requests: set[asyncio.Task] = set()
        self._stopping = False

    async def start(self) -> None:
        if not settings.weather_api_enabled:
            return
        self._stopping = False
        self._server = await asyncio.start_server(
            self._handle_client,
            settings.weather_api_host,
            settings.weather_api_port,
            limit=MAX_HEADERS,
        )
        addresses = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        logger.info("External weather API listening on {}", addresses)

    async def stop(self) -> None:
        self._stopping = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._server = None

        current = asyncio.current_task()
        active = [task for task in self._active_requests if task is not current and not task.done()]
        if not active:
            return
        _done, pending = await asyncio.wait(active, timeout=GRACEFUL_SHUTDOWN_TIMEOUT)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _authorized(headers: dict[str, str]) -> bool:
        token = settings.weather_api_token
        if not token:
            return False
        authorization = headers.get("authorization", "")
        candidate = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        candidate = candidate or headers.get("x-weather-api-key", "")
        return bool(candidate) and hmac.compare_digest(candidate, token)

    @staticmethod
    def _response(status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> bytes:
        reason = {
            200: "OK",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            405: "Method Not Allowed",
            408: "Request Timeout",
            429: "Too Many Requests",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }.get(status, "Error")
        return (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body

    @classmethod
    def _json_response(cls, status: int, payload: dict) -> bytes:
        return cls._response(
            status,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._active_requests.add(task)
        try:
            if self._stopping:
                await self._write(writer, self._json_response(503, {"ok": False, "error": "shutting_down"}))
                return
            await asyncio.wait_for(
                self._handle_request(reader, writer),
                timeout=settings.weather_api_timeout_seconds,
            )
        except asyncio.LimitOverrunError:
            await self._write(writer, self._json_response(400, {"ok": False, "error": "headers_too_large"}))
        except asyncio.TimeoutError:
            await self._write(writer, self._json_response(408, {"ok": False, "error": "request_timeout"}))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.exception("External weather API request failed")
            try:
                await self._write(writer, self._json_response(500, {"ok": False, "error": "internal_error"}))
            except Exception:
                pass
        finally:
            if task is not None:
                self._active_requests.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw_headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        if len(raw_headers) > MAX_HEADERS:
            await self._write(writer, self._json_response(400, {"ok": False, "error": "headers_too_large"}))
            return
        lines = raw_headers.decode("latin-1").split("\r\n")
        request_line = lines[0].split()
        if len(request_line) != 3:
            await self._write(writer, self._json_response(400, {"ok": False, "error": "invalid_request"}))
            return
        method, target, _version = request_line
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        if method.upper() != "GET":
            await self._write(writer, self._json_response(405, {"ok": False, "error": "method_not_allowed"}))
            return

        parsed = urlsplit(target)
        if parsed.path == "/healthz":
            await self._write(writer, self._json_response(200, {"ok": True, "service": "weather-api"}))
            return
        if not self._authorized(headers):
            await self._write(writer, self._json_response(401, {"ok": False, "error": "unauthorized"}))
            return

        supported_paths = {"/v1/weather", "/v1/weather/report", "/v1/weather/card.png"}
        if parsed.path not in supported_paths:
            await self._write(writer, self._json_response(404, {"ok": False, "error": "not_found"}))
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        location = (query.get("city") or query.get("location") or [""])[0].strip()
        if not location:
            await self._write(writer, self._json_response(400, {"ok": False, "error": "missing_city"}))
            return
        if len(location) > 100:
            await self._write(writer, self._json_response(400, {"ok": False, "error": "city_too_long"}))
            return

        include = {item.strip().lower() for item in (query.get("include") or [""])[0].split(",") if item.strip()}
        invalid_include = include - {"report", "image"}
        if invalid_include:
            await self._write(writer, self._json_response(400, {"ok": False, "error": "invalid_include"}))
            return
        if parsed.path == "/v1/weather/report":
            include = {"report"}

        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=CONCURRENCY_ACQUIRE_TIMEOUT)
        except asyncio.TimeoutError:
            await self._write(writer, self._json_response(429, {"ok": False, "error": "too_many_requests"}))
            return
        try:
            if parsed.path == "/v1/weather/card.png":
                status, body, content_type = await self._card_image(location)
                await self._write(writer, self._response(status, body, content_type))
                return
            status, payload = await self._weather_payload(location, include)
            await self._write(writer, self._json_response(status, payload))
        finally:
            self._semaphore.release()

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, response: bytes) -> None:
        writer.write(response)
        await writer.drain()

    async def _fetch(self, location: str, *, profile: str = "full", timeout: float | None = None):
        return await asyncio.wait_for(
            self.weather_service.get_fused_weather(location, profile=profile),
            timeout=timeout if timeout is not None else settings.weather_api_timeout_seconds,
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - asyncio.get_running_loop().time())

    @staticmethod
    def _report_payload(result) -> dict:
        if isinstance(result, str):
            # Compatibility for lightweight test doubles and older adapters
            # that still expose only the Telegram-oriented string method.
            return {"available": bool(result.strip()), "text": result or None}
        if getattr(result, "available", False):
            return {"available": True, "text": getattr(result, "text", None)}
        return {
            "available": False,
            "error": getattr(result, "error", None) or "report_failed",
            "text": None,
        }

    async def _weather_payload(self, location: str, include: set[str]) -> tuple[int, dict]:
        deadline = asyncio.get_running_loop().time() + settings.weather_api_timeout_seconds
        try:
            profile = "card" if include == {"image"} else "full"
            data = await self._fetch(location, profile=profile, timeout=self._remaining(deadline))
        except asyncio.TimeoutError:
            return 408, {"ok": False, "error": "weather_timeout"}
        except Exception:
            logger.exception("Weather API provider request failed for {}", location)
            return 503, {"ok": False, "error": "weather_provider_unavailable"}
        if data is None:
            return 404, {"ok": False, "error": "city_not_found", "city": location}

        card = {
            "format": "telegram-rich-blocks",
            "blocks": build_weather_blocks(data),
            "fallback_text": format_weather_response(data),
            "image_path": f"/v1/weather/card.png?city={quote(location, safe='')}",
        }
        payload = {
            "ok": True,
            "location": data.location_name,
            "updated_at": data.update_time.isoformat(),
            "card": card,
            "weather": data.model_dump(mode="json"),
        }
        if "image" in include:
            try:
                image = await asyncio.wait_for(
                    asyncio.to_thread(render_weather_card, data),
                    timeout=self._remaining(deadline),
                )
            except asyncio.TimeoutError:
                return 408, {"ok": False, "error": "image_timeout"}
            payload["image"] = {
                "media_type": "image/png",
                "base64": base64.b64encode(image).decode("ascii"),
            }
        if "report" in include:
            if self.llm_service.provider is None:
                payload["report"] = {"available": False, "error": "report_unavailable", "text": None}
            else:
                try:
                    result_factory = getattr(self.llm_service, "generate_weather_report_result", None)
                    if result_factory is None:
                        report = await asyncio.wait_for(
                            self.llm_service.generate_weather_report(data),
                            timeout=self._remaining(deadline),
                        )
                        payload["report"] = {"available": True, "text": report}
                    else:
                        result = await asyncio.wait_for(
                            result_factory(data),
                            timeout=self._remaining(deadline),
                        )
                        payload["report"] = self._report_payload(result)
                except asyncio.TimeoutError:
                    payload["report"] = {"available": False, "error": "report_timeout", "text": None}
                except Exception:
                    logger.exception("Weather API report generation failed for {}", location)
                    payload["report"] = {"available": False, "error": "report_failed", "text": None}
        return 200, payload

    async def _card_image(self, location: str) -> tuple[int, bytes, str]:
        try:
            data = await self._fetch(location, profile="card")
        except asyncio.TimeoutError:
            return 408, b"weather_timeout", "text/plain; charset=utf-8"
        except Exception:
            logger.exception("Weather card provider request failed for {}", location)
            return 503, b"weather_provider_unavailable", "text/plain; charset=utf-8"
        if data is None:
            return 404, b"city_not_found", "text/plain; charset=utf-8"
        return 200, await asyncio.to_thread(render_weather_card, data), "image/png"

__all__ = ["ExternalWeatherApi"]
