"""Testy fingerprintowania -- P0, najwazniejsze!"""

from monolynx.services.fingerprint import compute_fingerprint


class TestFingerprintGeneration:
    def test_same_exception_same_location_same_fingerprint(self):
        """Dwa identyczne bledy -> ten sam fingerprint."""
        exc1 = {
            "type": "ValueError",
            "value": "invalid literal for int()",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "process_order"}]},
        }
        exc2 = {
            "type": "ValueError",
            "value": "invalid literal for int()",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "process_order"}]},
        }
        assert compute_fingerprint(exc1) == compute_fingerprint(exc2)

    def test_same_exception_different_location_different_fingerprint(self):
        """Ten sam typ bledu z innej funkcji -> rozny fingerprint."""
        exc1 = {
            "type": "ValueError",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "process_order"}]},
        }
        exc2 = {
            "type": "ValueError",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "process_payment"}]},
        }
        assert compute_fingerprint(exc1) != compute_fingerprint(exc2)

    def test_different_exception_same_location_different_fingerprint(self):
        """Rozne typy bledow z tej samej lokalizacji -> rozne fingerprint."""
        exc1 = {
            "type": "ValueError",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "process_order"}]},
        }
        exc2 = {
            "type": "TypeError",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "process_order"}]},
        }
        assert compute_fingerprint(exc1) != compute_fingerprint(exc2)

    def test_fingerprint_ignores_exception_message(self):
        """Tresc komunikatu NIE wplywa na fingerprint."""
        exc1 = {
            "type": "ValueError",
            "value": "first error message",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "handler"}]},
        }
        exc2 = {
            "type": "ValueError",
            "value": "totally different message",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "handler"}]},
        }
        assert compute_fingerprint(exc1) == compute_fingerprint(exc2)

    def test_fingerprint_is_deterministic(self):
        """Wielokrotne wywolanie -> zawsze ten sam wynik."""
        exc = {
            "type": "RuntimeError",
            "stacktrace": {"frames": [{"filename": "app/utils.py", "function": "do_work"}]},
        }
        results = {compute_fingerprint(exc) for _ in range(100)}
        assert len(results) == 1

    def test_empty_stacktrace_falls_back_to_type_only(self):
        """Brak stacktrace -> fingerprint na podstawie samego typu."""
        exc = {"type": "ValueError", "stacktrace": {"frames": []}}
        result = compute_fingerprint(exc)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex

    def test_filters_site_packages_frames(self):
        """Ramki z site-packages sa ignorowane."""
        exc1 = {
            "type": "ValueError",
            "stacktrace": {
                "frames": [
                    {
                        "filename": "/venv/lib/python3.12/site-packages/django/views.py",
                        "function": "dispatch",
                    },
                    {"filename": "app/views.py", "function": "handler"},
                ]
            },
        }
        exc2 = {
            "type": "ValueError",
            "stacktrace": {
                "frames": [
                    {"filename": "app/views.py", "function": "handler"},
                ]
            },
        }
        assert compute_fingerprint(exc1) == compute_fingerprint(exc2)

    def test_missing_type_defaults_to_unknown(self):
        """Brak typu -> UnknownError."""
        exc = {"stacktrace": {"frames": []}}
        result = compute_fingerprint(exc)
        assert isinstance(result, str)
        assert len(result) == 64
