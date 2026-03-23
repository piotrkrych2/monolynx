#!/usr/bin/env python3
"""Generate 10 realistic error events for acme-app project in Monolynx.

Usage:
    python generate_errors.py <API_KEY> [--url https://open.monolynx.com]
"""

import json
import sys
import urllib.request

ERRORS = [
    {
        "exception": {
            "type": "ValueError",
            "value": "Invalid payment amount: -15.99",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/exception.py", "function": "inner", "lineno": 55},
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/checkout/views.py", "function": "post", "lineno": 45, "context_line": "return self.create_order(request.data)"},
                {"filename": "app/api/v2/checkout/views.py", "function": "create_order", "lineno": 87, "context_line": "payment_result = payment_service.process_payment(order.total)"},
                {"filename": "app/services/payment.py", "function": "process_payment", "lineno": 142, "context_line": "raise ValueError(f'Invalid payment amount: {amount}')"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/checkout/create", "method": "POST"},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "web-prod-03", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "stripe.error.CardError",
            "value": "Your card was declined. Your request was in live mode, but used a known test card.",
            "module": "stripe.error",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/checkout/views.py", "function": "post", "lineno": 52, "context_line": "charge = stripe_service.charge(order)"},
                {"filename": "app/services/stripe_client.py", "function": "charge", "lineno": 78, "context_line": "return stripe.Charge.create(**params)"},
                {"filename": "stripe/_api_requestor.py", "function": "request", "lineno": 314},
                {"filename": "stripe/_api_requestor.py", "function": "handle_error_response", "lineno": 156, "context_line": "raise err"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/checkout/pay", "method": "POST"},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "web-prod-01", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "ConnectionRefusedError",
            "value": "[Errno 111] Connection refused",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/notifications/views.py", "function": "send_notification", "lineno": 34, "context_line": "redis_client.publish(channel, payload)"},
                {"filename": "redis/client.py", "function": "publish", "lineno": 5765},
                {"filename": "redis/connection.py", "function": "connect", "lineno": 275, "context_line": "raise ConnectionRefusedError(str(e))"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/notifications/send", "method": "POST"},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "web-prod-02", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "PermissionError",
            "value": "User 'usr_3f8a' does not have permission to access resource 'org_settings'",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/middleware/rbac.py", "function": "check_permissions", "lineno": 62, "context_line": "raise PermissionError(f\"User '{user_id}' does not have permission to access resource '{resource}'\")"},
                {"filename": "app/api/v2/settings/views.py", "function": "get", "lineno": 28, "context_line": "self.check_org_admin(request.user)"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/settings/organization", "method": "GET"},
        "environment": "production",
        "release": "v2.4.1",
        "user": {"id": "usr_3f8a", "email": "jane.doe@example.com"},
        "server": {"hostname": "web-prod-01", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "sqlalchemy.exc.IntegrityError",
            "value": "duplicate key value violates unique constraint \"uq_user_email\"\nDETAIL: Key (email)=(john@acme.com) already exists.",
            "module": "sqlalchemy.exc",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/auth/views.py", "function": "register", "lineno": 55, "context_line": "user = user_service.create_user(data)"},
                {"filename": "app/services/user_service.py", "function": "create_user", "lineno": 31, "context_line": "db.session.commit()"},
                {"filename": "sqlalchemy/engine/default.py", "function": "do_execute", "lineno": 924},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/auth/register", "method": "POST"},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "web-prod-03", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "TimeoutError",
            "value": "Request to external geocoding API timed out after 10s",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/addresses/views.py", "function": "validate_address", "lineno": 41, "context_line": "result = geocoding_service.geocode(address_str)"},
                {"filename": "app/services/geocoding.py", "function": "geocode", "lineno": 67, "context_line": "response = httpx.get(url, params=params, timeout=10)"},
                {"filename": "httpx/_client.py", "function": "send", "lineno": 914, "context_line": "raise TimeoutError(f'Request to external geocoding API timed out after {timeout}s')"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/addresses/validate", "method": "POST"},
        "environment": "production",
        "release": "v2.4.0",
        "server": {"hostname": "web-prod-01", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "KeyError",
            "value": "'shipping_address'",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/orders/views.py", "function": "create", "lineno": 73, "context_line": "shipping = data['shipping_address']"},
                {"filename": "app/services/order_service.py", "function": "prepare_shipment", "lineno": 112, "context_line": "addr = order_data['shipping_address']"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/orders", "method": "POST", "data": {"items": [{"sku": "WIDGET-01", "qty": 2}]}},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "web-prod-02", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "jwt.ExpiredSignatureError",
            "value": "Signature has expired",
            "module": "jwt.exceptions",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/middleware/jwt_auth.py", "function": "authenticate", "lineno": 38, "context_line": "payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])"},
                {"filename": "jwt/api_jwt.py", "function": "decode", "lineno": 210},
                {"filename": "jwt/api_jwt.py", "function": "_validate_claims", "lineno": 285, "context_line": "raise ExpiredSignatureError('Signature has expired')"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/dashboard/stats", "method": "GET", "headers": {"Authorization": "Bearer eyJ..."}},
        "environment": "production",
        "release": "v2.4.1",
        "user": {"id": "usr_8b2c", "email": "bob@acme.com"},
        "server": {"hostname": "web-prod-01", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "celery.exceptions.MaxRetriesExceededError",
            "value": "Can't retry app.tasks.send_invoice_email[abc123] max retries exceeded (3)",
            "module": "celery.exceptions",
            "stacktrace": {"frames": [
                {"filename": "celery/app/task.py", "function": "apply_async", "lineno": 425},
                {"filename": "app/tasks/email.py", "function": "send_invoice_email", "lineno": 45, "context_line": "self.retry(exc=exc, countdown=60)"},
                {"filename": "celery/app/task.py", "function": "retry", "lineno": 712, "context_line": "raise MaxRetriesExceededError(...)"},
            ]},
        },
        "request": {"url": "https://acme-app.com/internal/worker", "method": "TASK"},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "worker-prod-01", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
    {
        "exception": {
            "type": "TypeError",
            "value": "Object of type Decimal is not JSON serializable",
            "stacktrace": {"frames": [
                {"filename": "django/core/handlers/base.py", "function": "_get_response", "lineno": 197},
                {"filename": "app/api/v2/reports/views.py", "function": "get", "lineno": 89, "context_line": "return JsonResponse(report_data)"},
                {"filename": "django/http/response.py", "function": "__init__", "lineno": 684},
                {"filename": "json/encoder.py", "function": "default", "lineno": 180, "context_line": "raise TypeError(f'Object of type {type(o).__name__} is not JSON serializable')"},
            ]},
        },
        "request": {"url": "https://acme-app.com/api/v2/reports/revenue?period=monthly", "method": "GET"},
        "environment": "production",
        "release": "v2.4.1",
        "server": {"hostname": "web-prod-03", "python_version": "3.12.1", "django_version": "5.1.2"},
    },
]


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <API_KEY> [--url URL]")
        sys.exit(1)

    api_key = sys.argv[1]
    base_url = "https://open.monolynx.com"

    if "--url" in sys.argv:
        idx = sys.argv.index("--url")
        base_url = sys.argv[idx + 1].rstrip("/")

    url = f"{base_url}/api/v1/events"
    ok, fail = 0, 0

    for i, error in enumerate(ERRORS, 1):
        data = json.dumps(error).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Monolynx-Key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
                print(f"  [{i}/10] OK — {error['exception']['type']}: {error['exception']['value'][:60]}  (issue: {body.get('id', '?')})")
                ok += 1
        except Exception as e:
            print(f"  [{i}/10] FAIL — {error['exception']['type']}: {e}")
            fail += 1

    print(f"\nDone: {ok} sent, {fail} failed")


if __name__ == "__main__":
    main()
