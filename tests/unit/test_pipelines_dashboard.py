"""Testy jednostkowe helperow dashboardu Pipelines (_build_row, _build_sprint_info_map)."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest

from monolynx.constants import PIPELINE_STEP_NAME_LABELS, PIPELINE_STEP_STATUS_LABELS
from monolynx.dashboard.pipelines import _build_row
from monolynx.models.pipeline import Pipeline, PipelineStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(name: str, position: int, status: str = "pending") -> PipelineStep:
    """Tworzy mock PipelineStep z podanymi atrybutami."""
    step = MagicMock(spec=PipelineStep)
    step.name = name
    step.position = position
    step.status = status
    return step


def _make_pipeline(
    pipeline_type: str = "ticket_work",
    status: str = "created",
    steps: list[PipelineStep] | None = None,
    ticket_id: uuid.UUID | None = None,
    sprint_id: uuid.UUID | None = None,
) -> Pipeline:
    """Tworzy mock Pipeline ze wskazanymi stepami."""
    pipeline = MagicMock(spec=Pipeline)
    pipeline.id = uuid.uuid4()
    pipeline.status = status
    pipeline.pipeline_type = pipeline_type
    pipeline.steps = steps or []
    pipeline.ticket_id = ticket_id
    pipeline.sprint_id = sprint_id
    pipeline.branch = None
    pipeline.created_at = None
    pipeline.started_at = None
    pipeline.finished_at = None
    return pipeline


# ---------------------------------------------------------------------------
# _build_row: ticket_work (3 stepy w kolejnosci)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRowTicketWork:
    def _make_ticket_work_pipeline(self, statuses: list[str] | None = None) -> Pipeline:
        """Pipeline ticket_work z 3 stepami (research/coding/wrap-up, position 0/1/2)."""
        if statuses is None:
            statuses = ["pending", "pending", "pending"]
        steps = [
            _make_step("research", 0, statuses[0]),
            _make_step("coding", 1, statuses[1]),
            _make_step("wrap-up", 2, statuses[2]),
        ]
        return _make_pipeline(pipeline_type="ticket_work", steps=steps)

    def test_ticket_work_returns_three_steps(self):
        """ticket_work z 3 stepami -> dict['steps'] ma 3 wpisy."""
        pipeline = self._make_ticket_work_pipeline()

        result = _build_row(pipeline, ticket_key="TST-1")

        assert len(result["steps"]) == 3

    def test_ticket_work_step_names_mapped_by_labels(self):
        """Nazwy stepow sa mapowane przez PIPELINE_STEP_NAME_LABELS (regresja AC #2)."""
        pipeline = self._make_ticket_work_pipeline()

        result = _build_row(pipeline, ticket_key="TST-1")

        steps = result["steps"]
        assert steps[0]["name"] == PIPELINE_STEP_NAME_LABELS["research"]
        assert steps[1]["name"] == PIPELINE_STEP_NAME_LABELS["coding"]
        assert steps[2]["name"] == PIPELINE_STEP_NAME_LABELS["wrap-up"]

    def test_ticket_work_step_names_exact_values(self):
        """Konkretne wartosci: Research, Coding, Wrap-up."""
        pipeline = self._make_ticket_work_pipeline()

        result = _build_row(pipeline, ticket_key="TST-1")

        names = [s["name"] for s in result["steps"]]
        assert names == ["Research", "Coding", "Wrap-up"]

    def test_ticket_work_steps_in_position_order(self):
        """Stepy sa w kolejnosci position 0->1->2."""
        pipeline = self._make_ticket_work_pipeline()

        result = _build_row(pipeline, ticket_key="TST-1")

        steps = result["steps"]
        # research = position 0, coding = 1, wrap-up = 2
        assert steps[0]["name"] == "Research"
        assert steps[1]["name"] == "Coding"
        assert steps[2]["name"] == "Wrap-up"

    def test_ticket_work_step_status_label_mapped(self):
        """status_label jest mapowany przez PIPELINE_STEP_STATUS_LABELS."""
        steps = [
            _make_step("research", 0, "running"),
            _make_step("coding", 1, "success"),
            _make_step("wrap-up", 2, "pending"),
        ]
        pipeline = _make_pipeline(pipeline_type="ticket_work", steps=steps)

        result = _build_row(pipeline, ticket_key="TST-1")

        assert result["steps"][0]["status_label"] == PIPELINE_STEP_STATUS_LABELS["running"]
        assert result["steps"][1]["status_label"] == PIPELINE_STEP_STATUS_LABELS["success"]
        assert result["steps"][2]["status_label"] == PIPELINE_STEP_STATUS_LABELS["pending"]

    def test_ticket_work_step_has_dot_class(self):
        """Kazdy step ma klucz dot_class."""
        pipeline = self._make_ticket_work_pipeline(["running", "pending", "pending"])

        result = _build_row(pipeline, ticket_key="TST-1")

        for step in result["steps"]:
            assert "dot_class" in step
        # running -> animate-pulse
        assert "animate-pulse" in result["steps"][0]["dot_class"]

    def test_ticket_work_sprint_name_in_result(self):
        """sprint_name przekazany do _build_row trafia do dict."""
        pipeline = self._make_ticket_work_pipeline()

        result = _build_row(pipeline, ticket_key="TST-1", sprint_name="Sprint Q2")

        assert result["sprint_name"] == "Sprint Q2"

    def test_ticket_work_sprint_name_none_when_not_provided(self):
        """Gdy brak sprint_name, dict ma sprint_name=None."""
        pipeline = self._make_ticket_work_pipeline()

        result = _build_row(pipeline, ticket_key="TST-1")

        assert result["sprint_name"] is None


# ---------------------------------------------------------------------------
# _build_row: sprint_close (2 stepy: wiki-update + wrap-up)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRowSprintClose:
    def _make_sprint_close_pipeline(self) -> Pipeline:
        steps = [
            _make_step("wiki-update", 0, "pending"),
            _make_step("wrap-up", 1, "pending"),
        ]
        return _make_pipeline(pipeline_type="sprint_close", steps=steps)

    def test_sprint_close_returns_two_steps(self):
        """sprint_close z 2 stepami -> dict['steps'] ma 2 wpisy."""
        pipeline = self._make_sprint_close_pipeline()

        result = _build_row(pipeline, ticket_key=None)

        assert len(result["steps"]) == 2

    def test_sprint_close_wiki_update_name_label(self):
        """wiki-update -> 'Aktualizacja Wiki'."""
        pipeline = self._make_sprint_close_pipeline()

        result = _build_row(pipeline, ticket_key=None)

        assert result["steps"][0]["name"] == "Aktualizacja Wiki"

    def test_sprint_close_wrap_up_name_label(self):
        """wrap-up -> 'Wrap-up'."""
        pipeline = self._make_sprint_close_pipeline()

        result = _build_row(pipeline, ticket_key=None)

        assert result["steps"][1]["name"] == "Wrap-up"

    def test_sprint_close_step_order(self):
        """wiki-update (position 0) przed wrap-up (position 1)."""
        pipeline = self._make_sprint_close_pipeline()

        result = _build_row(pipeline, ticket_key=None)

        names = [s["name"] for s in result["steps"]]
        assert names == ["Aktualizacja Wiki", "Wrap-up"]

    def test_sprint_close_sprint_name_in_result(self):
        """sprint_name przekazany trafia do dict."""
        pipeline = self._make_sprint_close_pipeline()

        result = _build_row(pipeline, ticket_key=None, sprint_name="Sprint Q1 2026")

        assert result["sprint_name"] == "Sprint Q1 2026"


# ---------------------------------------------------------------------------
# _build_row: sortowanie stepow (nieposortowane wejscie)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRowSorting:
    def test_steps_unsorted_input_sorted_output(self):
        """Stepy z position 2,0,1 wejsciowo -> wyjscie posortowane po position."""
        steps = [
            _make_step("wrap-up", 2, "pending"),
            _make_step("research", 0, "pending"),
            _make_step("coding", 1, "pending"),
        ]
        pipeline = _make_pipeline(pipeline_type="ticket_work", steps=steps)

        result = _build_row(pipeline, ticket_key="TST-2")

        names = [s["name"] for s in result["steps"]]
        assert names == ["Research", "Coding", "Wrap-up"]

    def test_steps_reverse_order_sorted(self):
        """Stepy podane odwrotnie (2,1,0) -> wyjscie 0,1,2."""
        steps = [
            _make_step("wrap-up", 2),
            _make_step("coding", 1),
            _make_step("research", 0),
        ]
        pipeline = _make_pipeline(pipeline_type="ticket_work", steps=steps)

        result = _build_row(pipeline, ticket_key="TST-3")

        assert result["steps"][0]["name"] == "Research"
        assert result["steps"][1]["name"] == "Coding"
        assert result["steps"][2]["name"] == "Wrap-up"


# ---------------------------------------------------------------------------
# _build_row: pusta lista stepow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRowEmptySteps:
    def test_empty_steps_returns_empty_list(self):
        """Pusta lista stepow -> dict['steps'] == []."""
        pipeline = _make_pipeline(steps=[])

        result = _build_row(pipeline, ticket_key="TST-4")

        assert result["steps"] == []

    def test_none_steps_returns_empty_list(self):
        """None jako steps -> dict['steps'] == [] (bez AttributeError)."""
        pipeline = _make_pipeline(steps=None)

        result = _build_row(pipeline, ticket_key="TST-4")

        assert result["steps"] == []


# ---------------------------------------------------------------------------
# _build_row: klucze wynikowe
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRowOutputKeys:
    def test_result_has_required_keys(self):
        """dict wynikowy ma wszystkie oczekiwane klucze."""
        pipeline = _make_pipeline(steps=[])

        result = _build_row(pipeline, ticket_key="TST-5", ticket_title="Test Ticket", triggered_by_name="Jan Kowalski")

        expected_keys = {
            "id",
            "status",
            "status_label",
            "status_class",
            "pipeline_type",
            "type_label",
            "ticket_key",
            "ticket_title",
            "ticket_id",
            "triggered_by_name",
            "branch",
            "created_at",
            "started_at",
            "started_iso",
            "finished_at",
            "duration",
            "is_running",
            "is_stale",
            "steps",
            "sprint_name",
        }
        assert expected_keys.issubset(result.keys())

    def test_ticket_key_and_title_passed_through(self):
        """ticket_key i ticket_title sa przekazywane do wyniku."""
        pipeline = _make_pipeline(steps=[])

        result = _build_row(pipeline, ticket_key="MON-42", ticket_title="Moj ticket")

        assert result["ticket_key"] == "MON-42"
        assert result["ticket_title"] == "Moj ticket"

    def test_triggered_by_name_passed_through(self):
        """triggered_by_name trafia do wyniku."""
        pipeline = _make_pipeline(steps=[])

        result = _build_row(pipeline, ticket_key=None, triggered_by_name="Anna Nowak")

        assert result["triggered_by_name"] == "Anna Nowak"


# ---------------------------------------------------------------------------
# _build_sprint_info_map: test integracyjny z DB
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSprintInfoMap:
    async def test_empty_sprint_ids_returns_empty_dict(self, db_session):
        """Pusta lista sprint_ids -> {}."""
        from monolynx.dashboard.pipelines import _build_sprint_info_map

        result = await _build_sprint_info_map(db_session, [])

        assert result == {}

    async def test_maps_sprint_id_to_name(self, db_session):
        """Istniejacy sprint -> {sprint.id: sprint.name}."""
        from monolynx.dashboard.pipelines import _build_sprint_info_map
        from monolynx.models.project import Project
        from monolynx.models.sprint import Sprint

        project = Project(
            name="Sprint Map Test",
            slug=f"sprint-map-{uuid.uuid4().hex[:6]}",
            code=("S" + uuid.uuid4().hex[:4]).upper(),
            api_key=uuid.uuid4().hex,
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Testowy Mapowanie",
            start_date=date.today(),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        result = await _build_sprint_info_map(db_session, [sprint.id])

        assert result == {sprint.id: "Sprint Testowy Mapowanie"}

    async def test_nonexistent_sprint_id_not_in_result(self, db_session):
        """Nieistniejacy sprint_id -> nie pojawia sie w wyniku."""
        from monolynx.dashboard.pipelines import _build_sprint_info_map

        fake_id = uuid.uuid4()
        result = await _build_sprint_info_map(db_session, [fake_id])

        assert fake_id not in result
        assert result == {}
