"""PayloadBuilder -- budowanie JSON payload z wyjatku i requestu."""

from __future__ import annotations

import platform
import socket
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from open_sentry_sdk.config import Config
from open_sentry_sdk.fingerprint import compute_fingerprint
from open_sentry_sdk.utils import (
    extract_user_info,
    get_client_ip,
    sanitize_data,
    sanitize_headers,
)

SDK_NAME = "open-sentry-python"
SDK_VERSION = "0.1.0"


class PayloadBuilder:
    def __init__(self, config: Config) -> None:
        self.config = config

    def build_from_exception(
        self,
        exception: BaseException,
        request: object | None = None,
    ) -> dict[str, Any]:
        exception_data = self._extract_exception_data(exception)
        fingerprint = compute_fingerprint(exception_data)

        payload: dict[str, Any] = {
            "event_id": uuid.uuid4().hex,
            "timestamp": datetime.now(UTC).isoformat(),
            "platform": "python",
            "sdk": {"name": SDK_NAME, "version": SDK_VERSION},
            "fingerprint": fingerprint,
            "level": "error",
            "environment": self.config.environment,
            "release": self.config.release,
            "exception": exception_data,
            "server": self._extract_server_info(),
        }

        if request is not None:
            request_data = self._extract_request_data(request)
            if request_data:
                payload["request"] = request_data
            user_data = extract_user_info(request)
            if user_data:
                payload["user"] = user_data

        return payload

    def build_from_message(
        self,
        message: str,
        level: str = "error",
        request: object | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": uuid.uuid4().hex,
            "timestamp": datetime.now(UTC).isoformat(),
            "platform": "python",
            "sdk": {"name": SDK_NAME, "version": SDK_VERSION},
            "level": level,
            "environment": self.config.environment,
            "release": self.config.release,
            "message": message,
            "exception": {"type": "Message", "value": message, "stacktrace": {"frames": []}},
            "fingerprint": compute_fingerprint({"type": "Message", "value": message}),
            "server": self._extract_server_info(),
        }

        if request is not None:
            request_data = self._extract_request_data(request)
            if request_data:
                payload["request"] = request_data

        return payload

    def _extract_exception_data(self, exception: BaseException) -> dict[str, Any]:
        tb = exception.__traceback__
        frames: list[dict[str, Any]] = []

        if tb is not None:
            for frame_summary in traceback.extract_tb(tb):
                frames.append(
                    {
                        "filename": frame_summary.filename,
                        "function": frame_summary.name,
                        "lineno": frame_summary.lineno,
                        "context_line": frame_summary.line or "",
                    }
                )

        return {
            "type": type(exception).__name__,
            "value": str(exception),
            "module": type(exception).__module__,
            "stacktrace": {"frames": frames},
        }

    def _extract_request_data(self, request: object) -> dict[str, Any] | None:
        try:
            method = getattr(request, "method", "")
            path = getattr(request, "path", "")
            meta = getattr(request, "META", {})

            scheme = meta.get("wsgi.url_scheme", "http")
            host = meta.get("HTTP_HOST", "localhost")
            url = f"{scheme}://{host}{path}"

            headers: dict[str, str] = {}
            for key, value in meta.items():
                if key.startswith("HTTP_"):
                    header_name = key[5:].replace("_", "-").lower()
                    headers[header_name] = value

            query_string = meta.get("QUERY_STRING", "")

            body = ""
            try:
                raw_body = getattr(request, "body", b"")
                if isinstance(raw_body, bytes):
                    body = raw_body[: self.config.max_body_size].decode(
                        "utf-8", errors="replace"
                    )
            except Exception:
                pass

            data: dict[str, Any] = {
                "url": url,
                "method": method,
                "headers": sanitize_headers(headers),
                "query_string": query_string,
                "body": body,
                "client_ip": get_client_ip(request),
            }

            # Sanitize POST data
            try:
                post_data = getattr(request, "POST", None)
                if post_data:
                    data["data"] = sanitize_data(dict(post_data))
            except Exception:
                pass

            return data
        except Exception:
            return None

    def _extract_server_info(self) -> dict[str, str]:
        info: dict[str, str] = {
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "python_version": platform.python_version(),
        }
        try:
            import django

            info["django_version"] = django.get_version()
        except ImportError:
            pass

        return info
