"""Testy integracyjne API v2 -- Projects (MON-142).

Pokrywa 11 endpointów `src/monolynx/api/v2/projects.py`: list/get/create/
update/delete projektu, invite/list/remove członka, summary, list/create
etykiety. Wołane serwisy: `services/projects.py` (MON-137).

Uprawnienia z parytetem MCP (patrz KONTRAKT API w opisie ticketu): tylko
update_project (settings:write), delete_project (settings:delete),
invite_member (users:write), remove_member (users:delete) sprawdzają
`require_permission()` w routerze (`api/v2/common.py`) -- PRZED wywołaniem
serwisu, który ma własny, redundantny `check_permission()` z INNYM tekstem
komunikatu. Rola 'member' (DEFAULT_ROLE_PERMISSIONS w constants.py) ma
`settings: ["read"]` i `users: ["read"]` -- bez write/delete -- więc służy
jako scenariusz 403 dla wszystkich czterech. Testy 403 asertują dokładny
`detail` z `require_permission()` ("Brak uprawnienia {module}:{action}"), nie
tylko status -- inaczej mutant usuwający sprawdzenie w routerze przeżywa,
maskowany identycznym statusem 403 z serwisu (patrz notatka w MEMORY.md).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.label import Label
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.auth import hash_password

from .conftest import add_member, make_actor, make_owner_project

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# GET /api/v2/projects
# ---------------------------------------------------------------------------


class TestListProjects:
    async def test_returns_only_projects_of_caller(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-mine")
        _other_actor, _other_project = await make_owner_project(db_session, "list-other")

        response = await client.get("/api/v2/projects", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        slugs = [item["slug"] for item in body["items"]]
        assert project.slug in slugs
        assert body["page"] == 1
        assert body["per_page"] == 100
        assert body["total"] == len(body["items"])
        assert body["total_pages"] == 1

    async def test_project_fields(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-fields")

        response = await client.get("/api/v2/projects", headers=actor.headers)

        item = next(i for i in response.json()["items"] if i["slug"] == project.slug)
        assert item["id"] == str(project.id)
        assert item["name"] == project.name
        assert item["code"] == project.code
        assert item["description"] == project.description

    async def test_requires_authorization(self, client):
        response = await client.get("/api/v2/projects")

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{project_slug}
# ---------------------------------------------------------------------------


class TestGetProject:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-happy")

        response = await client.get(f"/api/v2/projects/{project.slug}", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(project.id)
        assert body["slug"] == project.slug
        assert body["name"] == project.name
        assert body["code"] == project.code

    async def test_nonexistent_project_returns_404(self, client, db_session):
        actor = await make_actor(db_session, "get-404")

        response = await client.get("/api/v2/projects/nie-ma-takiego-projektu", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_non_member_returns_403(self, client, db_session):
        _owner_actor, project = await make_owner_project(db_session, "get-403")
        outsider = await make_actor(db_session, "get-403-outsider")

        response = await client.get(f"/api/v2/projects/{project.slug}", headers=outsider.headers)

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# POST /api/v2/projects
# ---------------------------------------------------------------------------


class TestCreateProject:
    async def test_creates_project_and_makes_caller_owner(self, client, db_session):
        actor = await make_actor(db_session, "create")

        response = await client.post(
            "/api/v2/projects",
            headers=actor.headers,
            json={"name": "Nowy Projekt API v2"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Nowy Projekt API v2"
        assert body["slug"] == "nowy-projekt-api-v2"

        db_project = (await db_session.execute(select(Project).where(Project.slug == "nowy-projekt-api-v2"))).scalar_one()
        assert db_project.is_active is True

    async def test_invalid_name_returns_422(self, client, db_session):
        actor = await make_actor(db_session, "create-invalid")

        response = await client.post("/api/v2/projects", headers=actor.headers, json={"name": "   "})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{project_slug}
# ---------------------------------------------------------------------------


class TestUpdateProject:
    async def test_happy_path_updates_name(self, client, db_session):
        actor, project = await make_owner_project(db_session, "update-happy")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}",
            headers=actor.headers,
            json={"name": "Zmieniona nazwa"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Zmieniona nazwa"

        await db_session.refresh(project)
        assert project.name == "Zmieniona nazwa"

    async def test_member_without_settings_write_gets_403(self, client, db_session):
        """Serwis update_project() TEZ zwraca 403 dla tego samego uprawnienia
        (check_permission wewnatrz), wiec sam kod statusu nie odroznia routera
        od serwisu -- mutant "usun require_permission() z routera" nadal
        przechodziłby przez serwis i dawal 403. Asercja na detail z
        require_permission() w api/v2/common.py ("Brak uprawnienia {module}:
        {action}") celuje wprost w warstwe routera."""
        _actor, project = await make_owner_project(db_session, "update-403")
        member_actor = await make_actor(db_session, "update-403-member")
        await add_member(db_session, project, member_actor.user, role="member")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}",
            headers=member_actor.headers,
            json={"name": "Nie powinno przejsc"},
        )

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Brak uprawnienia settings:write"
        await db_session.refresh(project)
        assert project.name != "Nie powinno przejsc"


# ---------------------------------------------------------------------------
# DELETE /api/v2/projects/{project_slug}
# ---------------------------------------------------------------------------


class TestDeleteProject:
    async def test_happy_path_soft_deletes(self, client, db_session):
        actor, project = await make_owner_project(db_session, "delete-happy")

        response = await client.delete(f"/api/v2/projects/{project.slug}", headers=actor.headers)

        assert response.status_code == 204
        assert response.content == b""

        await db_session.refresh(project)
        assert project.is_active is False

    async def test_member_without_settings_delete_gets_403(self, client, db_session):
        """Jak w TestUpdateProject -- serwis delete_project() ma wlasny
        check_permission i tez zwrociłby 403, wiec detail rozroznia router
        od serwisu (patrz reguła w MEMORY.md: dwie warstwy, ta sama odmowa,
        asercja celuje w efekt konkretnej warstwy)."""
        _actor, project = await make_owner_project(db_session, "delete-403")
        member_actor = await make_actor(db_session, "delete-403-member")
        await add_member(db_session, project, member_actor.user, role="member")

        response = await client.delete(f"/api/v2/projects/{project.slug}", headers=member_actor.headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "Brak uprawnienia settings:delete"
        await db_session.refresh(project)
        assert project.is_active is True


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{project_slug}/members
# ---------------------------------------------------------------------------


class TestInviteMember:
    async def test_new_email_creates_account_and_sends_invitation(self, client, db_session):
        actor, project = await make_owner_project(db_session, "invite-new")

        with patch("monolynx.api.v2.projects.send_invitation_email") as mock_send:
            response = await client.post(
                f"/api/v2/projects/{project.slug}/members",
                headers=actor.headers,
                json={"email": "nowy-czlonek@example.com", "role": "member"},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "nowy-czlonek@example.com"
        assert body["role"] == "member"
        assert body["is_new_user"] is True
        mock_send.assert_called_once()

    async def test_existing_active_user_added_without_email(self, client, db_session):
        actor, project = await make_owner_project(db_session, "invite-existing")
        existing_actor = await make_actor(db_session, "invite-existing-target")
        await db_session.flush()

        with patch("monolynx.api.v2.projects.send_invitation_email") as mock_send:
            response = await client.post(
                f"/api/v2/projects/{project.slug}/members",
                headers=actor.headers,
                json={"email": existing_actor.user.email, "role": "member"},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["is_new_user"] is False
        mock_send.assert_not_called()

    async def test_member_without_users_write_gets_403(self, client, db_session):
        """Serwis invite_member() ma wlasny check_permission (users:write) i
        tez zwrociłby 403 -- detail rozroznia router od serwisu (patrz reguła
        w MEMORY.md)."""
        _actor, project = await make_owner_project(db_session, "invite-403")
        member_actor = await make_actor(db_session, "invite-403-member")
        await add_member(db_session, project, member_actor.user, role="member")

        with patch("monolynx.api.v2.projects.send_invitation_email") as mock_send:
            response = await client.post(
                f"/api/v2/projects/{project.slug}/members",
                headers=member_actor.headers,
                json={"email": "nie-powinien-powstac@example.com", "role": "member"},
            )

        assert response.status_code == 403
        assert response.json()["detail"] == "Brak uprawnienia users:write"
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{project_slug}/members
# ---------------------------------------------------------------------------


class TestListMembers:
    async def test_happy_path_fields(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-members")

        response = await client.get(f"/api/v2/projects/{project.slug}/members", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        item = next(m for m in body["items"] if m["email"] == actor.user.email)
        assert item["user_id"] == str(actor.user.id)
        assert item["name"]
        assert item["role"] == "owner"
        assert "joined_at" in item

    async def test_pagination_per_page_default_100_and_override(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-members-page")
        for i in range(3):
            extra = await make_actor(db_session, f"list-members-page-{i}")
            await add_member(db_session, project, extra.user, role="member")

        default_response = await client.get(f"/api/v2/projects/{project.slug}/members", headers=actor.headers)
        assert default_response.json()["per_page"] == 100
        assert len(default_response.json()["items"]) == 4

        page1 = await client.get(f"/api/v2/projects/{project.slug}/members?per_page=1&page=1", headers=actor.headers)
        page2 = await client.get(f"/api/v2/projects/{project.slug}/members?per_page=1&page=2", headers=actor.headers)
        assert page1.json()["total_pages"] == 4
        assert page1.json()["items"] != page2.json()["items"]


# ---------------------------------------------------------------------------
# DELETE /api/v2/projects/{project_slug}/members/{member_id}
# ---------------------------------------------------------------------------


class TestRemoveMember:
    async def test_happy_path_removes_member_by_user_id(self, client, db_session):
        actor, project = await make_owner_project(db_session, "remove-happy")
        target = await make_actor(db_session, "remove-happy-target")
        await add_member(db_session, project, target.user, role="member")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{target.user.id}",
            headers=actor.headers,
        )

        assert response.status_code == 204
        assert response.content == b""

        list_response = await client.get(f"/api/v2/projects/{project.slug}/members", headers=actor.headers)
        remaining_ids = {m["user_id"] for m in list_response.json()["items"]}
        assert str(target.user.id) not in remaining_ids

    async def test_removes_member_whose_email_has_uppercase_letters_stored_in_db(self, client, db_session):
        """MON-228 A4: konto z dashboard/users/create moze miec e-mail z wielkimi literami. Router
        znajdowal czlonka po user_id, ale usuwal przez e-mail zamieniony na lowercase -> 422
        'nie istnieje w systemie' mimo ze czlonek byl na liscie."""
        actor, project = await make_owner_project(db_session, "remove-mixed-case")
        target = User(email=f"Mixed.Case.{uuid.uuid4().hex[:8]}@Example.COM", password_hash=hash_password("testpass123"))
        db_session.add(target)
        await db_session.flush()
        await add_member(db_session, project, target, role="member")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{target.id}",
            headers=actor.headers,
        )

        assert response.status_code == 204
        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
        ).scalar_one_or_none()
        assert remaining is None

    async def test_removes_only_the_account_with_given_user_id_when_emails_differ_by_case(self, client, db_session):
        actor, project = await make_owner_project(db_session, "remove-twin")
        local = f"twin.{uuid.uuid4().hex[:8]}"
        lower = User(email=f"{local}@example.com", password_hash=hash_password("testpass123"))
        upper = User(email=f"{local.upper()}@EXAMPLE.COM", password_hash=hash_password("testpass123"))
        db_session.add_all([lower, upper])
        await db_session.flush()
        await add_member(db_session, project, lower, role="member")
        await add_member(db_session, project, upper, role="member")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{upper.id}",
            headers=actor.headers,
        )

        assert response.status_code == 204
        rows = (await db_session.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))).scalars().all()
        assert upper.id not in rows
        assert lower.id in rows

    async def test_removing_owner_returns_422_and_keeps_membership(self, client, db_session):
        actor, project = await make_owner_project(db_session, "remove-owner")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{actor.user.id}",
            headers=actor.headers,
        )

        assert response.status_code == 422
        assert "Nie mozna usunac ownera projektu" in response.json()["detail"]
        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == actor.user.id))
        ).scalar_one_or_none()
        assert remaining is not None

    async def test_unknown_member_id_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "remove-404")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{uuid.uuid4()}",
            headers=actor.headers,
        )

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_member_without_users_delete_gets_403(self, client, db_session):
        """Serwis remove_member() ma wlasny check_permission (users:delete) i
        tez zwrociłby 403 -- detail rozroznia router od serwisu (patrz reguła
        w MEMORY.md)."""
        actor, project = await make_owner_project(db_session, "remove-403")
        member_actor = await make_actor(db_session, "remove-403-member")
        await add_member(db_session, project, member_actor.user, role="member")
        target = await make_actor(db_session, "remove-403-target")
        await add_member(db_session, project, target.user, role="member")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{target.user.id}",
            headers=member_actor.headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Brak uprawnienia users:delete"
        list_response = await client.get(f"/api/v2/projects/{project.slug}/members", headers=actor.headers)
        remaining_ids = {m["user_id"] for m in list_response.json()["items"]}
        assert str(target.user.id) in remaining_ids

    async def test_permission_checked_before_member_lookup_returns_403_not_404(self, client, db_session):
        """Router remove_member sprawdza require_permission(users:delete) PRZED
        odnalezieniem czlonka po member_id (parytet z MCP: brak uprawnienia
        wygrywa nad brakiem zasobu). member_id jest tu losowym UUID, ktory nie
        odpowiada zadnemu czlonkowi projektu -- gdyby kolejnosc byla odwrotna
        (najpierw lookup), odpowiedz byłaby 404 "nie istnieje", nie 403."""
        _actor, project = await make_owner_project(db_session, "remove-403-order")
        member_actor = await make_actor(db_session, "remove-403-order-member")
        await add_member(db_session, project, member_actor.user, role="member")

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{uuid.uuid4()}",
            headers=member_actor.headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Brak uprawnienia users:delete"


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{project_slug}/summary
# ---------------------------------------------------------------------------


class TestGetProjectSummary:
    async def test_returns_200_json_object(self, client, db_session):
        actor, project = await make_owner_project(db_session, "summary")

        response = await client.get(f"/api/v2/projects/{project.slug}/summary", headers=actor.headers)

        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    async def test_non_member_returns_403(self, client, db_session):
        _actor, project = await make_owner_project(db_session, "summary-403")
        outsider = await make_actor(db_session, "summary-403-outsider")

        response = await client.get(f"/api/v2/projects/{project.slug}/summary", headers=outsider.headers)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{project_slug}/labels
# ---------------------------------------------------------------------------


class TestListLabels:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-labels")
        label = Label(project_id=project.id, name="bug-apiv2", color="#e74c3c")
        db_session.add(label)
        await db_session.flush()

        response = await client.get(f"/api/v2/projects/{project.slug}/labels", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        item = next(i for i in body["items"] if i["id"] == str(label.id))
        assert item["name"] == "bug-apiv2"
        assert item["color"] == "#e74c3c"
        assert body["per_page"] == 100


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{project_slug}/labels
# ---------------------------------------------------------------------------


class TestCreateLabel:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-label")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/labels",
            headers=actor.headers,
            json={"name": "feature-apiv2", "color": "#2ecc71"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "feature-apiv2"
        assert body["color"] == "#2ecc71"

        db_label = (await db_session.execute(select(Label).where(Label.project_id == project.id, Label.name == "feature-apiv2"))).scalar_one()
        assert db_label.color == "#2ecc71"

    async def test_duplicate_name_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-label-dup")
        db_session.add(Label(project_id=project.id, name="dup-apiv2", color="#6c757d"))
        await db_session.flush()

        response = await client.post(
            f"/api/v2/projects/{project.slug}/labels",
            headers=actor.headers,
            json={"name": "dup-apiv2"},
        )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# Parytet member_id z list_members (kryterium akceptacji)
# ---------------------------------------------------------------------------


class TestRemoveMemberIdMatchesListMembers:
    async def test_member_id_field_is_user_id_from_list_members(self, client, db_session):
        """DELETE .../members/{member_id} przyjmuje user_id, ten sam co pole
        'user_id' zwracane przez GET .../members (nie id wiersza ProjectMember)."""
        actor, project = await make_owner_project(db_session, "remove-matches-list")
        target = await make_actor(db_session, "remove-matches-list-target")
        member_row = await add_member(db_session, project, target.user, role="member")
        assert member_row.id != target.user.id  # dowod ze to dwa rozne UUID

        list_response = await client.get(f"/api/v2/projects/{project.slug}/members", headers=actor.headers)
        member_id_from_list = next(m["user_id"] for m in list_response.json()["items"] if m["email"] == target.user.email)

        response = await client.delete(
            f"/api/v2/projects/{project.slug}/members/{member_id_from_list}",
            headers=actor.headers,
        )

        assert response.status_code == 204
