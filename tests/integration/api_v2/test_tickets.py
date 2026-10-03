"""Testy integracyjne API v2 -- Tickets (MON-142).

Pokrywa 14 endpointów `src/monolynx/api/v2/tickets.py`: tickety (list/search/
get/create/update/delete/bulk), board, komentarze, kryteria akceptacji.
Wołane serwisy: `services/tickets.py` (MON-137), `services/ticket_dependencies.py
::set_blockers` dla blokerów.

Kluczowe ryzyko z ticketu: trasy statyczne `/tickets/search` i `/tickets/bulk`
muszą być zarejestrowane PRZED `/tickets/{ticket_id}`, inaczej FastAPI dopasuje
"search"/"bulk" jako ticket_id i zwróci 404 z resolve_ticket_uuid zamiast
trafić do właściwego handlera -- testowane wprost niżej.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, select

from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.ticket_dependency import TicketDependency
from monolynx.models.wiki_page import WikiPage

from .conftest import make_actor, make_owner_project, make_project

pytestmark = pytest.mark.integration


async def _make_ticket(db_session, project, number: int, **kwargs) -> Ticket:
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=kwargs.pop("title", f"Ticket #{number}"),
        status=kwargs.pop("status", "todo"),
        priority=kwargs.pop("priority", "medium"),
        **kwargs,
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _set_blockers(client, actor, project, ticket, blockers) -> None:
    response = await client.patch(
        f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
        headers=actor.headers,
        json={"blocked_by_ids": [str(b.id) for b in blockers]},
    )
    assert response.status_code == 200


async def _make_spec_page(db_session, project, user, *, slug: str = "spec") -> WikiPage:
    page = WikiPage(
        project_id=project.id,
        title="Specyfikacja ticketu",
        slug=slug,
        minio_path=f"wiki/{project.slug}/{slug}.md",
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    db_session.add(page)
    await db_session.flush()
    return page


async def _make_sprint(db_session, project, **kwargs) -> Sprint:
    sprint = Sprint(
        project_id=project.id,
        name=kwargs.pop("name", "Sprint testowy"),
        start_date=kwargs.pop("start_date", date(2026, 1, 1)),
        status=kwargs.pop("status", "planning"),
        **kwargs,
    )
    db_session.add(sprint)
    await db_session.flush()
    return sprint


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/tickets
# ---------------------------------------------------------------------------


class TestListTickets:
    async def test_happy_path_fields(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-tickets")
        ticket = await _make_ticket(db_session, project, 1, title="Pierwszy ticket", priority="high", story_points=5)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        item = next(t for t in body["items"] if t["id"] == str(ticket.id))
        assert item["key"] == f"{project.code}-1"
        assert item["title"] == "Pierwszy ticket"
        assert item["status"] == "todo"
        assert item["priority"] == "high"
        assert item["story_points"] == 5

    async def test_default_per_page_is_20(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-tickets-page")
        # 25 ticketow, wstawiane numerami rosnaco -- serwis sortuje po order/created_at,
        # nie po numerze, wiec to nie ustawia oczekiwanej kolejnosci wyniku (rule #2).
        for i in range(1, 26):
            await _make_ticket(db_session, project, i)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=actor.headers)

        body = response.json()
        assert body["per_page"] == 20
        assert len(body["items"]) == 20
        assert body["total"] == 25
        assert body["total_pages"] == 2

    async def test_second_page_returns_remaining_items(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-tickets-page2")
        for i in range(1, 26):
            await _make_ticket(db_session, project, i)

        page2 = await client.get(f"/api/v2/projects/{project.slug}/tickets?page=2", headers=actor.headers)

        assert page2.json()["page"] == 2
        assert len(page2.json()["items"]) == 5

    async def test_item_without_blockers_has_empty_blocked_by(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-no-blockers")
        ticket = await _make_ticket(db_session, project, 1)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=actor.headers)

        item = next(t for t in response.json()["items"] if t["id"] == str(ticket.id))
        assert item["blocked_by"] == []

    async def test_item_with_blocker_returns_id_key_title_status(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-blocker-shape")
        blocker = await _make_ticket(db_session, project, 1, title="Bloker listy", status="in_progress")
        blocked = await _make_ticket(db_session, project, 2, title="Zablokowany listy")
        await _set_blockers(client, actor, project, blocked, [blocker])

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=actor.headers)

        item = next(t for t in response.json()["items"] if t["id"] == str(blocked.id))
        assert item["blocked_by"] == [
            {"id": str(blocker.id), "key": f"{project.code}-1", "title": "Bloker listy", "status": "in_progress"},
        ]

    async def test_each_item_gets_its_own_blockers(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-blockers-distinct")
        blocker_a = await _make_ticket(db_session, project, 1, title="Bloker A")
        blocker_b = await _make_ticket(db_session, project, 2, title="Bloker B")
        blocker_c = await _make_ticket(db_session, project, 3, title="Bloker C")
        blocked_x = await _make_ticket(db_session, project, 4, title="Zablokowany X")
        blocked_y = await _make_ticket(db_session, project, 5, title="Zablokowany Y")
        free = await _make_ticket(db_session, project, 6, title="Wolny")
        await _set_blockers(client, actor, project, blocked_x, [blocker_a])
        await _set_blockers(client, actor, project, blocked_y, [blocker_b, blocker_c])

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=actor.headers)

        by_id = {t["id"]: t for t in response.json()["items"]}
        assert [b["id"] for b in by_id[str(blocked_x.id)]["blocked_by"]] == [str(blocker_a.id)]
        assert {b["id"] for b in by_id[str(blocked_y.id)]["blocked_by"]} == {str(blocker_b.id), str(blocker_c.id)}
        assert by_id[str(free.id)]["blocked_by"] == []
        assert by_id[str(blocker_a.id)]["blocked_by"] == []

    @pytest.mark.parametrize("query", ["", "?compact=true"], ids=["default", "compact"])
    async def test_blocker_loading_does_not_scale_queries_with_tickets(self, client, db_session, engine, query):
        actor, project = await make_owner_project(db_session, "list-blockers-nplus1")
        base = await _make_ticket(db_session, project, 1, title="Wspolny bloker")
        for number in range(2, 5):
            ticket = await _make_ticket(db_session, project, number)
            await _set_blockers(client, actor, project, ticket, [base])

        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _count)
        try:
            first = await client.get(f"/api/v2/projects/{project.slug}/tickets{query}", headers=actor.headers)
            queries_with_few_rows = len(statements)

            for number in range(5, 10):
                ticket = await _make_ticket(db_session, project, number)
                await _set_blockers(client, actor, project, ticket, [base])
            statements.clear()
            second = await client.get(f"/api/v2/projects/{project.slug}/tickets{query}", headers=actor.headers)
            queries_with_more_rows = len(statements)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _count)

        assert len(first.json()["items"]) == 4
        assert len(second.json()["items"]) == 9
        assert sum(1 for t in first.json()["items"] if t["blocked_by"]) == 3
        assert sum(1 for t in second.json()["items"] if t["blocked_by"]) == 8
        assert queries_with_more_rows == queries_with_few_rows

    async def test_non_member_returns_403(self, client, db_session):
        _actor, project = await make_owner_project(db_session, "list-tickets-403")
        outsider = await make_actor(db_session, "list-tickets-403-out")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=outsider.headers)

        assert response.status_code == 403


class TestListTicketsCompact:
    async def test_compact_true_item_has_no_description_key(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-no-desc")
        ticket = await _make_ticket(db_session, project, 1, title="Z opisem", description="Dlugi opis ticketu")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets?compact=true", headers=actor.headers)

        assert response.status_code == 200
        item = next(t for t in response.json()["items"] if t["id"] == str(ticket.id))
        assert "description" not in item

    async def test_compact_true_keeps_every_other_key_of_default_item(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-keys")
        await _make_ticket(db_session, project, 1, title="Z opisem", description="Opis", priority="high", story_points=3)

        default = await client.get(f"/api/v2/projects/{project.slug}/tickets", headers=actor.headers)
        compact = await client.get(f"/api/v2/projects/{project.slug}/tickets?compact=true", headers=actor.headers)

        default_item = default.json()["items"][0]
        compact_item = compact.json()["items"][0]
        assert set(compact_item) == set(default_item) - {"description"}
        assert compact_item == {key: value for key, value in default_item.items() if key != "description"}

    async def test_compact_true_keeps_blocked_by_with_blocker_key(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-blockers")
        blocker = await _make_ticket(db_session, project, 1, title="Bloker compact", status="in_progress", description="Opis blokera")
        blocked = await _make_ticket(db_session, project, 2, title="Zablokowany compact", description="Opis zablokowanego")
        await _set_blockers(client, actor, project, blocked, [blocker])

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets?compact=true", headers=actor.headers)

        item = next(t for t in response.json()["items"] if t["id"] == str(blocked.id))
        assert item["blocked_by"] == [
            {"id": str(blocker.id), "key": f"{project.code}-1", "title": "Bloker compact", "status": "in_progress"},
        ]

    async def test_compact_true_keeps_pagination_envelope(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-envelope")
        for i in range(1, 26):
            await _make_ticket(db_session, project, i, description=f"Opis {i}")

        default = await client.get(f"/api/v2/projects/{project.slug}/tickets?page=2", headers=actor.headers)
        compact = await client.get(f"/api/v2/projects/{project.slug}/tickets?page=2&compact=true", headers=actor.headers)

        default_body = default.json()
        compact_body = compact.json()
        assert {k: v for k, v in compact_body.items() if k != "items"} == {k: v for k, v in default_body.items() if k != "items"}
        assert compact_body["page"] == 2
        assert compact_body["per_page"] == 20
        assert compact_body["total"] == 25
        assert len(compact_body["items"]) == 5

    @pytest.mark.parametrize("query", ["", "?compact=false"], ids=["no-param", "compact-false"])
    async def test_default_and_compact_false_keep_description_value(self, client, db_session, query):
        actor, project = await make_owner_project(db_session, "compact-default-desc")
        ticket = await _make_ticket(db_session, project, 1, description="Opis w odpowiedzi domyslnej")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets{query}", headers=actor.headers)

        item = next(t for t in response.json()["items"] if t["id"] == str(ticket.id))
        assert item["description"] == "Opis w odpowiedzi domyslnej"

    @pytest.mark.parametrize("query", ["", "?compact=false"], ids=["no-param", "compact-false"])
    async def test_default_and_compact_false_item_without_description_has_null_key(self, client, db_session, query):
        actor, project = await make_owner_project(db_session, "compact-default-null")
        ticket = await _make_ticket(db_session, project, 1, description=None)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets{query}", headers=actor.headers)

        item = next(t for t in response.json()["items"] if t["id"] == str(ticket.id))
        assert "description" in item
        assert item["description"] is None

    async def test_compact_true_item_without_description_has_no_description_key(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-null-desc")
        ticket = await _make_ticket(db_session, project, 1, description=None)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets?compact=true", headers=actor.headers)

        item = next(t for t in response.json()["items"] if t["id"] == str(ticket.id))
        assert "description" not in item

    async def test_create_response_keeps_description(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-create-desc")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets",
            headers=actor.headers,
            json={"title": "Z opisem", "description": "Opis po utworzeniu"},
        )

        assert response.status_code == 201
        assert response.json()["description"] == "Opis po utworzeniu"

    async def test_update_response_keeps_description(self, client, db_session):
        actor, project = await make_owner_project(db_session, "compact-update-desc")
        ticket = await _make_ticket(db_session, project, 1, description="Opis przed zmiana")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"title": "Nowy tytul"},
        )

        assert response.status_code == 200
        assert response.json()["description"] == "Opis przed zmiana"

    def test_compact_model_fields_equal_list_item_fields_without_description(self):
        from monolynx.schemas.api_v2.ticket import TicketListItem, TicketListItemCompact

        assert list(TicketListItemCompact.model_fields) == [name for name in TicketListItem.model_fields if name != "description"]

    async def test_openapi_describes_compact_query_param_and_compact_schema(self, client):
        response = await client.get("/api/v2/openapi.json")

        assert response.status_code == 200
        spec = response.json()
        paths = [p for p in spec["paths"] if p.endswith("/projects/{project_slug}/tickets")]
        assert len(paths) == 1, list(spec["paths"])
        compact_params = [p for p in spec["paths"][paths[0]]["get"]["parameters"] if p["name"] == "compact"]
        assert len(compact_params) == 1
        compact_param = compact_params[0]
        assert compact_param["in"] == "query"
        assert compact_param["required"] is False
        assert compact_param["schema"]["type"] == "boolean"
        assert compact_param["schema"]["default"] is False
        properties = spec["components"]["schemas"]["TicketListItemCompact"]["properties"]
        assert "blocked_by" in properties
        assert "description" not in properties


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/tickets/search -- MUSI trafic przed {ticket_id}
# ---------------------------------------------------------------------------


class TestSearchTickets:
    async def test_reaches_search_handler_not_ticket_id_404(self, client, db_session):
        """Regresja tras: /tickets/search nie wolno dopasowac jako ticket_id
        'search' (co dalby resolve_ticket_uuid -> 404)."""
        actor, project = await make_owner_project(db_session, "search-routing")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/search", headers=actor.headers)

        assert response.status_code == 200
        assert "items" in response.json()

    async def test_happy_path_filters_by_query(self, client, db_session):
        actor, project = await make_owner_project(db_session, "search-happy")
        matching = await _make_ticket(db_session, project, 1, title="Naprawic logowanie")
        await _make_ticket(db_session, project, 2, title="Zupelnie inny temat")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/search?query=logowanie", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        ids = [item["id"] for item in body["items"]]
        assert str(matching.id) in ids
        assert body["per_page"] == 20

    async def test_default_per_page_is_20(self, client, db_session):
        actor, project = await make_owner_project(db_session, "search-page")
        for i in range(1, 22):
            await _make_ticket(db_session, project, i)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/search", headers=actor.headers)

        body = response.json()
        assert body["per_page"] == 20
        assert body["total"] == 21
        assert body["total_pages"] == 2


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{slug}/tickets/bulk -- MUSI trafic przed {ticket_id}
# ---------------------------------------------------------------------------


class TestBulkUpdateTickets:
    async def test_reaches_bulk_handler_not_ticket_id_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "bulk-routing")
        ticket = await _make_ticket(db_session, project, 1, status="todo")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/bulk",
            headers=actor.headers,
            json={"ticket_ids": [str(ticket.id)], "status": "in_progress"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 1
        assert body["failed"] == []

        await db_session.refresh(ticket)
        assert ticket.status == "in_progress"

    async def test_unknown_ticket_id_reported_in_failed(self, client, db_session):
        actor, project = await make_owner_project(db_session, "bulk-failed")
        bogus_id = str(uuid.uuid4())

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/bulk",
            headers=actor.headers,
            json={"ticket_ids": [bogus_id], "status": "done"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 0
        assert len(body["failed"]) == 1
        assert body["failed"][0]["id"] == bogus_id

    async def test_too_many_ticket_ids_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "bulk-too-many")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/bulk",
            headers=actor.headers,
            json={"ticket_ids": [str(uuid.uuid4()) for _ in range(101)]},
        )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/tickets/{ticket_id} -- UUID i klucz
# ---------------------------------------------------------------------------


class TestGetTicket:
    async def test_by_uuid(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-uuid")
        ticket = await _make_ticket(db_session, project, 1, title="Ticket po UUID")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["title"] == "Ticket po UUID"

    async def test_by_key(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-key")
        ticket = await _make_ticket(db_session, project, 7, title="Ticket po kluczu")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{project.code}-7", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(ticket.id)
        assert body["title"] == "Ticket po kluczu"

    async def test_nonexistent_uuid_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-404")

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_detail_includes_comments_criteria_blockers(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-detail")
        blocker = await _make_ticket(db_session, project, 1, title="Bloker")
        ticket = await _make_ticket(db_session, project, 2, title="Zablokowany", description="Opis")
        db_session.add(TicketComment(ticket_id=ticket.id, user_id=actor.user.id, content="Komentarz"))
        db_session.add(TicketAcceptanceCriterion(ticket_id=ticket.id, description="Kryterium", created_by_user_id=actor.user.id))
        await db_session.flush()

        # ustaw blocked_by przez PATCH (jedyna droga zapisu wg services/ticket_dependencies.py)
        patch_response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(blocker.id)]},
        )
        assert patch_response.status_code == 200

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}", headers=actor.headers)

        body = response.json()
        assert body["description"] == "Opis"
        assert len(body["comments"]) == 1
        assert len(body["acceptance_criteria"]) == 1
        assert [b["id"] for b in body["blocked_by"]] == [str(blocker.id)]

        blocker_response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{blocker.id}", headers=actor.headers)
        assert [b["id"] for b in blocker_response.json()["blocks"]] == [str(ticket.id)]

    async def test_detail_returns_spec_page_id_when_set(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-spec-set")
        spec = await _make_spec_page(db_session, project, actor.user)
        ticket = await _make_ticket(db_session, project, 1, spec_page_id=spec.id)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["spec_page_id"] == str(spec.id)

    async def test_detail_returns_null_spec_page_id_when_unset(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-spec-null")
        ticket = await _make_ticket(db_session, project, 1)

        response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}", headers=actor.headers)

        body = response.json()
        assert "spec_page_id" in body
        assert body["spec_page_id"] is None

    async def test_update_response_returns_spec_page_id(self, client, db_session):
        actor, project = await make_owner_project(db_session, "patch-spec")
        spec = await _make_spec_page(db_session, project, actor.user)
        ticket = await _make_ticket(db_session, project, 1, spec_page_id=spec.id)

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"title": "Nowy tytul"},
        )

        assert response.status_code == 200
        assert response.json()["spec_page_id"] == str(spec.id)

    async def test_create_response_returns_null_spec_page_id(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-spec-null")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets",
            headers=actor.headers,
            json={"title": "Bez spec"},
        )

        assert response.status_code == 201
        body = response.json()
        assert "spec_page_id" in body
        assert body["spec_page_id"] is None


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{slug}/tickets
# ---------------------------------------------------------------------------


class TestCreateTicket:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-ticket")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets",
            headers=actor.headers,
            json={"title": "Nowy ticket API v2", "priority": "high", "story_points": 3},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Nowy ticket API v2"
        assert body["priority"] == "high"
        assert body["story_points"] == 3
        assert body["status"] == "backlog"

    async def test_with_blocker_persists_relation(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-ticket-blocker")
        blocker = await _make_ticket(db_session, project, 1, title="Bloker")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets",
            headers=actor.headers,
            json={"title": "Zablokowany od startu", "blocked_by_ids": [str(blocker.id)]},
        )

        assert response.status_code == 201
        assert [b["id"] for b in response.json()["blocked_by"]] == [str(blocker.id)]

    async def test_empty_title_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-ticket-invalid")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets",
            headers=actor.headers,
            json={"title": "   "},
        )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{slug}/tickets/{ticket_id} -- semantyka PATCH i blocked_by_ids
# ---------------------------------------------------------------------------


class TestUpdateTicket:
    async def test_absent_field_does_not_change_it(self, client, db_session):
        actor, project = await make_owner_project(db_session, "patch-absent")
        ticket = await _make_ticket(db_session, project, 1, title="Oryginal", priority="high")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"title": "Zmieniony"},
        )

        assert response.status_code == 200
        assert response.json()["priority"] == "high"  # nie podane w body -- bez zmian

    async def test_blocked_by_ids_replace_all(self, client, db_session):
        actor, project = await make_owner_project(db_session, "patch-blockers-replace")
        blocker_a = await _make_ticket(db_session, project, 1, title="A")
        blocker_b = await _make_ticket(db_session, project, 2, title="B")
        ticket = await _make_ticket(db_session, project, 3, title="Zaleznosci")

        first = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(blocker_a.id)]},
        )
        assert [b["id"] for b in first.json()["blocked_by"]] == [str(blocker_a.id)]

        replaced = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(blocker_b.id)]},
        )
        blocked_ids = {b["id"] for b in replaced.json()["blocked_by"]}
        assert blocked_ids == {str(blocker_b.id)}  # A zastapione, nie dolozone

    async def test_blocked_by_ids_empty_list_clears(self, client, db_session):
        actor, project = await make_owner_project(db_session, "patch-blockers-clear")
        blocker = await _make_ticket(db_session, project, 1, title="Bloker")
        ticket = await _make_ticket(db_session, project, 2, title="Ma blokera")
        await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(blocker.id)]},
        )

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": []},
        )

        assert response.json()["blocked_by"] == []

    async def test_blocked_by_ids_absent_leaves_relation_unchanged(self, client, db_session):
        actor, project = await make_owner_project(db_session, "patch-blockers-absent")
        blocker = await _make_ticket(db_session, project, 1, title="Bloker")
        ticket = await _make_ticket(db_session, project, 2, title="Ma blokera")
        await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(blocker.id)]},
        )

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"title": "Inny tytul, bez blocked_by_ids w body"},
        )

        assert [b["id"] for b in response.json()["blocked_by"]] == [str(blocker.id)]

    async def test_cycle_rejected_without_committing_rest_of_submit(self, client, db_session):
        """A blokowany przez B; PATCH B z blocked_by_ids=[A] tworzylby cykl -> 422
        z komunikatem serwisu ("Cykl..."). Dowod ze POZOSTALE pola tego samego
        submitu (title, priority) NIE zostaly zapisane: kazde wywolanie
        `db_session.commit` (spy z side_effect, wraps=oryginal) zapisuje stan
        `(ticket_b.title, ticket_b.priority)` W CHWILI commita; zaden zapisany
        stan nie moze pokazywac nowych wartosci "B tytul zmieniony"/"high".

        WHY nie `assert_not_awaited`: `db.commit()` w TYM requescie jest
        wolany takze przez `get_api_user` (dependency autoryzacji) ->
        `services/mcp_auth.py::verify_mcp_token` aktualizuje `last_used_at`
        tokenu i commituje TO, zanim handler w ogole zacznie przetwarzac
        PATCH -- ten commit jest legalny i nie dotyczy ticketu B, wiec
        `assert_not_awaited()` byl falszywym alarmem (commit z auth jest
        jedynym wywolaniem, a handler nigdy nie dochodzi do wlasnego commita,
        bo `set_blockers` rzuca ValueError wczesniej). Test musi wiec
        odrozniac KTORY commit zaszedl, nie tylko czy jakikolwiek zaszedl --
        stad zapis stanu ticketu w momencie kazdego wywolania, nie sama
        liczba wywolan.

        WHY nie odczyt stanu po requescie (refresh/GET): routery API v2
        celowo NIE robia jawnego `db.rollback()` (decyzja TM) -- w produkcji
        `get_db()` po prostu porzuca sesje bez commita i Postgres sam
        wycofuje transakcje, wiec "brak commita PRZEZ HANDLER" jest
        kontraktem "nic z PATCH-a nie przetrwa", nie "brak jakiegokolwiek
        commita w requescie". `set_blockers` woła
        `db.execute(text("SELECT pg_advisory_xact_lock..."))` PRZED walidacja
        cyklu, co wyzwala autoflush i flushuje juz przypisane w Pythonie
        `ticket.title`/`ticket.priority` DO TEJ SAMEJ transakcji mimo
        pozniejszego bledu -- odczyt stanu w tym samym polaczeniu (refresh/
        GET) widzialby wiec flush sprzed bledu i nic by nie dowodzil (regula
        MON-207 z MEMORY.md: test commita musi padac na mutancie "commituj
        stan po przypisaniu pol, przed walidacja cyklu").

        Relacja blokerow B sprawdzana bezposrednim query na TicketDependency
        (nie przez GET/ticket.blocked_by), zgodnie z notatka MEMORY.md o
        TicketDependency -- omijanie serwisu przez ORM po re-query maskowaloby
        buga.
        """
        actor, project = await make_owner_project(db_session, "patch-cycle")
        ticket_a = await _make_ticket(db_session, project, 1, title="A")
        ticket_b = await _make_ticket(db_session, project, 2, title="B tytul oryginalny", priority="medium")
        setup = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket_a.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(ticket_b.id)]},
        )
        assert setup.status_code == 200

        original_commit = db_session.commit
        committed_states: list[tuple[str, str]] = []

        async def _record_state_then_commit(*args: object, **kwargs: object) -> None:
            committed_states.append((ticket_b.title, ticket_b.priority))
            await original_commit(*args, **kwargs)

        commit_spy = AsyncMock(side_effect=_record_state_then_commit)
        with patch.object(db_session, "commit", commit_spy):
            response = await client.patch(
                f"/api/v2/projects/{project.slug}/tickets/{ticket_b.id}",
                headers=actor.headers,
                json={
                    "blocked_by_ids": [str(ticket_a.id)],
                    "title": "B tytul zmieniony",
                    "priority": "high",
                },
            )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert "Cykl" in response.json()["detail"]
        assert committed_states  # przynajmniej commit auth zaszedl -- dowod ze spy dzialal
        assert all(title != "B tytul zmieniony" for title, _priority in committed_states)
        assert all(priority != "high" for _title, priority in committed_states)

        blockers_result = await db_session.execute(select(TicketDependency.blocker_id).where(TicketDependency.ticket_id == ticket_b.id))
        assert blockers_result.scalars().all() == []

    async def test_blocker_from_other_project_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "patch-foreign-blocker")
        other_project = await make_project(db_session, "patch-foreign-blocker-other")
        foreign_ticket = await _make_ticket(db_session, other_project, 1, title="Obcy")
        ticket = await _make_ticket(db_session, project, 1, title="Lokalny")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}",
            headers=actor.headers,
            json={"blocked_by_ids": [str(foreign_ticket.id)]},
        )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# DELETE /api/v2/projects/{slug}/tickets/{ticket_id}
# ---------------------------------------------------------------------------


class TestDeleteTicket:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "delete-ticket")
        ticket = await _make_ticket(db_session, project, 1)

        response = await client.delete(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}", headers=actor.headers)

        assert response.status_code == 204
        assert response.content == b""

        remaining = (await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))).scalar_one_or_none()
        assert remaining is None

    async def test_nonexistent_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "delete-ticket-404")

        response = await client.delete(f"/api/v2/projects/{project.slug}/tickets/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/board
# ---------------------------------------------------------------------------


class TestGetBoard:
    async def test_no_active_sprint_returns_null_sprint(self, client, db_session):
        actor, project = await make_owner_project(db_session, "board-empty")

        response = await client.get(f"/api/v2/projects/{project.slug}/board", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["sprint"] is None

    async def test_active_sprint_with_ticket(self, client, db_session):
        actor, project = await make_owner_project(db_session, "board-active")
        sprint = await _make_sprint(db_session, project, status="active")
        await _make_ticket(db_session, project, 1, title="Na tablicy", status="todo", sprint_id=sprint.id)

        response = await client.get(f"/api/v2/projects/{project.slug}/board", headers=actor.headers)

        body = response.json()
        assert body["sprint"] is not None
        assert body["sprint"]["name"] == sprint.name
        assert any(t["title"] == "Na tablicy" for t in body["columns"]["todo"])


# ---------------------------------------------------------------------------
# Komentarze
# ---------------------------------------------------------------------------


class TestComments:
    async def test_add_then_list(self, client, db_session):
        actor, project = await make_owner_project(db_session, "comments")
        ticket = await _make_ticket(db_session, project, 1)

        add_response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}/comments",
            headers=actor.headers,
            json={"content": "Pierwszy komentarz"},
        )
        assert add_response.status_code == 201
        assert add_response.json()["content"] == "Pierwszy komentarz"

        list_response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}/comments", headers=actor.headers)
        assert list_response.status_code == 200
        contents = [c["content"] for c in list_response.json()["items"]]
        assert "Pierwszy komentarz" in contents


# ---------------------------------------------------------------------------
# Kryteria akceptacji
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    async def test_add_then_list(self, client, db_session):
        actor, project = await make_owner_project(db_session, "criteria")
        ticket = await _make_ticket(db_session, project, 1)

        add_response = await client.post(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}/acceptance-criteria",
            headers=actor.headers,
            json={"description": "Kryterium jeden"},
        )
        assert add_response.status_code == 201
        criterion_id = add_response.json()["id"]
        assert add_response.json()["is_completed"] is False

        list_response = await client.get(f"/api/v2/projects/{project.slug}/tickets/{ticket.id}/acceptance-criteria", headers=actor.headers)
        descriptions = [c["description"] for c in list_response.json()["items"]]
        assert "Kryterium jeden" in descriptions
        assert any(c["id"] == criterion_id for c in list_response.json()["items"])

    async def test_update_marks_completed(self, client, db_session):
        actor, project = await make_owner_project(db_session, "criteria-update")
        ticket = await _make_ticket(db_session, project, 1)
        criterion = TicketAcceptanceCriterion(ticket_id=ticket.id, description="Do ukonczenia", created_by_user_id=actor.user.id)
        db_session.add(criterion)
        await db_session.flush()

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}/acceptance-criteria/{criterion.id}",
            headers=actor.headers,
            json={"is_completed": True},
        )

        assert response.status_code == 200
        assert response.json()["is_completed"] is True

    async def test_delete_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "criteria-delete")
        ticket = await _make_ticket(db_session, project, 1)
        criterion = TicketAcceptanceCriterion(ticket_id=ticket.id, description="Do usuniecia", created_by_user_id=actor.user.id)
        db_session.add(criterion)
        await db_session.flush()

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/tickets/{ticket.id}/acceptance-criteria/{criterion.id}",
            headers=actor.headers,
        )

        assert response.status_code == 204
        remaining = (
            await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion.id))
        ).scalar_one_or_none()
        assert remaining is None

    async def test_criterion_of_other_ticket_returns_404_on_update(self, client, db_session):
        actor, project = await make_owner_project(db_session, "criteria-cross-update")
        ticket_a = await _make_ticket(db_session, project, 1, title="A")
        ticket_b = await _make_ticket(db_session, project, 2, title="B")
        criterion = TicketAcceptanceCriterion(ticket_id=ticket_a.id, description="Nalezy do A", created_by_user_id=actor.user.id)
        db_session.add(criterion)
        await db_session.flush()

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/tickets/{ticket_b.id}/acceptance-criteria/{criterion.id}",
            headers=actor.headers,
            json={"description": "Probuje edytowac cudze"},
        )

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_criterion_of_other_ticket_returns_404_on_delete(self, client, db_session):
        actor, project = await make_owner_project(db_session, "criteria-cross-delete")
        ticket_a = await _make_ticket(db_session, project, 1, title="A")
        ticket_b = await _make_ticket(db_session, project, 2, title="B")
        criterion = TicketAcceptanceCriterion(ticket_id=ticket_a.id, description="Nalezy do A", created_by_user_id=actor.user.id)
        db_session.add(criterion)
        await db_session.flush()

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/tickets/{ticket_b.id}/acceptance-criteria/{criterion.id}",
            headers=actor.headers,
        )

        assert response.status_code == 404
        still_there = (
            await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion.id))
        ).scalar_one_or_none()
        assert still_there is not None
