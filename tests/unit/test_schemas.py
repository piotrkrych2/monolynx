"""Testy walidacji schematow Pydantic."""

import pytest
from pydantic import ValidationError

from monolynx.schemas.events import EventPayload, ExceptionData
from monolynx.schemas.issues import StatusUpdate


class TestEventPayload:
    def test_valid_minimal_payload(self):
        payload = EventPayload(exception=ExceptionData(type="ValueError", value="bad value"))
        assert payload.exception.type == "ValueError"
        assert payload.level == "error"
        assert payload.platform == "python"

    def test_valid_full_payload(self):
        payload = EventPayload(
            event_id="abc123",
            timestamp="2026-02-19T10:00:00Z",
            platform="python",
            level="error",
            environment="production",
            exception=ExceptionData(
                type="ValueError",
                value="bad",
                stacktrace={"frames": [{"filename": "app.py", "function": "f"}]},
            ),
        )
        assert payload.event_id == "abc123"
        assert payload.environment == "production"

    def test_missing_exception_raises_error(self):
        with pytest.raises(ValidationError):
            EventPayload()  # type: ignore[call-arg]


class TestStatusUpdate:
    def test_valid_status(self):
        update = StatusUpdate(status="resolved")
        assert update.status == "resolved"

    def test_any_string_accepted_by_schema(self):
        """Schema akceptuje dowolny string -- walidacja logiczna jest w endpoincie."""
        update = StatusUpdate(status="any_value")
        assert update.status == "any_value"
