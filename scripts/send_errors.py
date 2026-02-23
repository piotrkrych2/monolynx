#!/usr/bin/env python3
"""Skrypt wysylajacy przykladowe bledy na projekt Monolynx.

Uzycie:
    python scripts/send_errors.py --url http://localhost:8000 --key TWOJ_API_KEY
    python scripts/send_errors.py --url http://localhost:8000 --key TWOJ_API_KEY --count 20
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

ERRORS = [
    {
        "type": "ValueError",
        "value": "invalid literal for int() with base 10: 'abc'",
        "module": "builtins",
        "frames": [
            {"filename": "app/views.py", "function": "process_order", "lineno": 42,
             "context_line": "    quantity = int(request.POST['qty'])"},
        ],
    },
    {
        "type": "KeyError",
        "value": "'user_id'",
        "module": "builtins",
        "frames": [
            {"filename": "app/middleware.py", "function": "get_current_user", "lineno": 18,
             "context_line": "    uid = session['user_id']"},
            {"filename": "app/views.py", "function": "dashboard", "lineno": 55,
             "context_line": "    user = get_current_user(request)"},
        ],
    },
    {
        "type": "ZeroDivisionError",
        "value": "division by zero",
        "module": "builtins",
        "frames": [
            {"filename": "app/utils.py", "function": "calculate_average", "lineno": 12,
             "context_line": "    return total / count"},
            {"filename": "app/views.py", "function": "stats_view", "lineno": 88,
             "context_line": "    avg = calculate_average(values)"},
        ],
    },
    {
        "type": "AttributeError",
        "value": "'NoneType' object has no attribute 'email'",
        "module": "builtins",
        "frames": [
            {"filename": "app/services/notify.py", "function": "send_notification", "lineno": 33,
             "context_line": "    recipient = user.email"},
        ],
    },
    {
        "type": "PermissionError",
        "value": "[Errno 13] Permission denied: '/var/log/app.log'",
        "module": "builtins",
        "frames": [
            {"filename": "app/logging.py", "function": "setup_file_handler", "lineno": 7,
             "context_line": "    handler = open('/var/log/app.log', 'a')"},
        ],
    },
    {
        "type": "ConnectionError",
        "value": "Connection refused: redis://localhost:6379",
        "module": "redis.exceptions",
        "frames": [
            {"filename": "app/cache.py", "function": "get_cached", "lineno": 21,
             "context_line": "    return self.redis.get(key)"},
            {"filename": "app/views.py", "function": "product_detail", "lineno": 72,
             "context_line": "    data = cache.get_cached(f'product:{pk}')"},
        ],
    },
    {
        "type": "TypeError",
        "value": "expected str, got NoneType",
        "module": "builtins",
        "frames": [
            {"filename": "app/serializers.py", "function": "serialize_user", "lineno": 15,
             "context_line": "    return {'name': first + ' ' + last}"},
        ],
    },
    {
        "type": "IntegrityError",
        "value": "duplicate key value violates unique constraint \"users_email_key\"",
        "module": "sqlalchemy.exc",
        "frames": [
            {"filename": "app/views.py", "function": "register", "lineno": 110,
             "context_line": "    db.session.commit()"},
        ],
    },
]

URLS = [
    "https://example.com/orders/create",
    "https://example.com/dashboard",
    "https://example.com/api/v1/users",
    "https://example.com/products/42",
    "https://example.com/auth/register",
    "https://example.com/stats",
]

METHODS = ["GET", "POST", "PUT", "DELETE"]
LEVELS = ["error", "error", "error", "fatal", "warning"]
ENVIRONMENTS = ["production", "staging", "development"]


def build_payload(error: dict) -> dict:
    url = random.choice(URLS)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": "python",
        "level": random.choice(LEVELS),
        "environment": random.choice(ENVIRONMENTS),
        "exception": {
            "type": error["type"],
            "value": error["value"],
            "module": error.get("module"),
            "stacktrace": {"frames": error["frames"]},
        },
        "request": {
            "url": url,
            "method": random.choice(METHODS),
            "headers": {"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            "client_ip": f"192.168.1.{random.randint(1, 254)}",
        },
        "server": {
            "hostname": f"web-{random.randint(1, 3)}.example.com",
            "python_version": "3.12.1",
            "django_version": "5.0.2",
        },
    }


def send_event(url: str, api_key: str, payload: dict) -> bool:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=f"{url.rstrip('/')}/api/v1/events",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Monolynx-Key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return True
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.reason}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"  Blad polaczenia: {e.reason}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Wyslij przykladowe bledy do Monolynx")
    parser.add_argument("--url", required=True, help="URL serwera (np. http://localhost:8000)")
    parser.add_argument("--key", required=True, help="API key projektu")
    parser.add_argument("--count", type=int, default=10, help="Liczba bledow do wyslania (domyslnie 10)")
    args = parser.parse_args()

    ok = 0
    fail = 0
    for i in range(args.count):
        error = random.choice(ERRORS)
        payload = build_payload(error)
        print(f"[{i+1}/{args.count}] {error['type']}: {error['value'][:60]}...", end=" ")
        if send_event(args.url, args.key, payload):
            print("OK")
            ok += 1
        else:
            print("FAIL")
            fail += 1

    print(f"\nWyslano: {ok}, Bledy: {fail}")


if __name__ == "__main__":
    main()
