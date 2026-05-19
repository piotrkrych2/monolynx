"""Testy integracyjne -- filtrowanie i sortowanie listy issues (MON-65).

Pokrywane scenariusze:
- Domyslny filtr: tylko unresolved (bez parametrow)
- ?status=all -> wszystkie issues
- ?status=resolved -> tylko resolved
- ?status=ignored -> tylko ignored
- ?sort=event_count&order=desc -> sortowanie po event_count malejaco
- ?sort=last_seen&order=asc -> sortowanie po last_seen rosnaco
- ?status=garbage -> fallback do unresolved, HTTP 200 (nie 500)
- ?sort=garbage&order=invalid -> fallback do last_seen/desc, HTTP 200
- User niebedacy czlonkiem projektu -> 403 (require_permission)
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session


def _make_project(slug: str, name: str | None = None) -> Project:
    """Helper: tworzy projekt z unikalnym slug i kodem."""
    return Project(
        name=name or f"Project {slug}",
        slug=slug,
        code="F" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


def _make_issue(
    project_id,
    title: str,
    status: str,
    event_count: int = 1,
    fingerprint: str | None = None,
    last_seen: datetime | None = None,
) -> Issue:
    """Helper: tworzy Issue z minimalnymi polami."""
    fp = fingerprint or secrets.token_hex(12)
    return Issue(
        project_id=project_id,
        fingerprint=fp,
        title=title,
        status=status,
        event_count=event_count,
        last_seen=last_seen or datetime.now(UTC),
    )


@pytest.mark.integration
class TestIssueListDefaultFilter:
    """Domyslny filtr: bez parametrow -> tylko unresolved."""

    async def test_issue_list_default_shows_only_unresolved(self, client, db_session):
        """GET bez params -> tylko unresolved widoczne w HTML."""
        project = _make_project("flt-default")
        db_session.add(project)
        await db_session.flush()

        unresolved = _make_issue(project.id, "UnresolvedError: needs fix", "unresolved")
        resolved = _make_issue(project.id, "ResolvedError: already done", "resolved")
        ignored = _make_issue(project.id, "IgnoredError: known noise", "ignored")
        db_session.add_all([unresolved, resolved, ignored])
        await db_session.flush()

        await login_session(client, db_session, email="flt-default@test.com")

        # Act: GET bez parametrow
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")

        # Assert
        assert resp.status_code == 200
        assert "UnresolvedError: needs fix" in resp.text
        assert "ResolvedError: already done" not in resp.text
        assert "IgnoredError: known noise" not in resp.text


@pytest.mark.integration
class TestIssueListStatusFilter:
    """Filtrowanie po statusie przez ?status=."""

    async def test_issue_list_status_filter_all(self, client, db_session):
        """?status=all -> wszystkie 3 issues widoczne."""
        project = _make_project("flt-all")
        db_session.add(project)
        await db_session.flush()

        unresolved = _make_issue(project.id, "UnresolvedError: all-test", "unresolved")
        resolved = _make_issue(project.id, "ResolvedError: all-test", "resolved")
        ignored = _make_issue(project.id, "IgnoredError: all-test", "ignored")
        db_session.add_all([unresolved, resolved, ignored])
        await db_session.flush()

        await login_session(client, db_session, email="flt-all@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=all")

        assert resp.status_code == 200
        assert "UnresolvedError: all-test" in resp.text
        assert "ResolvedError: all-test" in resp.text
        assert "IgnoredError: all-test" in resp.text

    async def test_issue_list_status_filter_resolved(self, client, db_session):
        """?status=resolved -> tylko resolved widoczne."""
        project = _make_project("flt-resolved")
        db_session.add(project)
        await db_session.flush()

        unresolved = _make_issue(project.id, "UnresolvedError: res-test", "unresolved")
        resolved = _make_issue(project.id, "ResolvedError: res-test", "resolved")
        ignored = _make_issue(project.id, "IgnoredError: res-test", "ignored")
        db_session.add_all([unresolved, resolved, ignored])
        await db_session.flush()

        await login_session(client, db_session, email="flt-resolved@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=resolved")

        assert resp.status_code == 200
        assert "ResolvedError: res-test" in resp.text
        assert "UnresolvedError: res-test" not in resp.text
        assert "IgnoredError: res-test" not in resp.text

    async def test_issue_list_status_filter_ignored(self, client, db_session):
        """?status=ignored -> tylko ignored widoczne."""
        project = _make_project("flt-ignored")
        db_session.add(project)
        await db_session.flush()

        unresolved = _make_issue(project.id, "UnresolvedError: ign-test", "unresolved")
        resolved = _make_issue(project.id, "ResolvedError: ign-test", "resolved")
        ignored = _make_issue(project.id, "IgnoredError: ign-test", "ignored")
        db_session.add_all([unresolved, resolved, ignored])
        await db_session.flush()

        await login_session(client, db_session, email="flt-ignored@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=ignored")

        assert resp.status_code == 200
        assert "IgnoredError: ign-test" in resp.text
        assert "UnresolvedError: ign-test" not in resp.text
        assert "ResolvedError: ign-test" not in resp.text

    async def test_issue_list_status_filter_unresolved_explicit(self, client, db_session):
        """?status=unresolved (jawnie) -> tylko unresolved."""
        project = _make_project("flt-unres-expl")
        db_session.add(project)
        await db_session.flush()

        unresolved = _make_issue(project.id, "UnresolvedError: expl-test", "unresolved")
        resolved = _make_issue(project.id, "ResolvedError: expl-test", "resolved")
        db_session.add_all([unresolved, resolved])
        await db_session.flush()

        await login_session(client, db_session, email="flt-unres-expl@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=unresolved")

        assert resp.status_code == 200
        assert "UnresolvedError: expl-test" in resp.text
        assert "ResolvedError: expl-test" not in resp.text


@pytest.mark.integration
class TestIssueListSorting:
    """Sortowanie listy issues."""

    async def test_issue_list_sort_event_count_desc(self, client, db_session):
        """?sort=event_count&order=desc -> kolejnosc 5, 3, 1 w HTML."""
        project = _make_project("flt-sort-evt-desc")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        issue_low = _make_issue(
            project.id,
            "LowCount: 1 occurrence",
            "unresolved",
            event_count=1,
            last_seen=now - timedelta(hours=3),
        )
        issue_mid = _make_issue(
            project.id,
            "MidCount: 3 occurrences",
            "unresolved",
            event_count=3,
            last_seen=now - timedelta(hours=2),
        )
        issue_high = _make_issue(
            project.id,
            "HighCount: 5 occurrences",
            "unresolved",
            event_count=5,
            last_seen=now - timedelta(hours=1),
        )
        db_session.add_all([issue_low, issue_mid, issue_high])
        await db_session.flush()

        await login_session(client, db_session, email="flt-sort-evt-desc@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?sort=event_count&order=desc")

        assert resp.status_code == 200
        assert "HighCount: 5 occurrences" in resp.text
        assert "MidCount: 3 occurrences" in resp.text
        assert "LowCount: 1 occurrence" in resp.text

        # Sprawdz kolejnosc: high (5) przed mid (3) przed low (1)
        pos_high = resp.text.index("HighCount: 5 occurrences")
        pos_mid = resp.text.index("MidCount: 3 occurrences")
        pos_low = resp.text.index("LowCount: 1 occurrence")
        assert pos_high < pos_mid < pos_low

    async def test_issue_list_sort_event_count_asc(self, client, db_session):
        """?sort=event_count&order=asc -> kolejnosc 1, 3, 5 w HTML."""
        project = _make_project("flt-sort-evt-asc")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        issue_low = _make_issue(
            project.id,
            "AscLow: 1 event",
            "unresolved",
            event_count=1,
            last_seen=now,
        )
        issue_mid = _make_issue(
            project.id,
            "AscMid: 3 events",
            "unresolved",
            event_count=3,
            last_seen=now,
        )
        issue_high = _make_issue(
            project.id,
            "AscHigh: 5 events",
            "unresolved",
            event_count=5,
            last_seen=now,
        )
        db_session.add_all([issue_low, issue_mid, issue_high])
        await db_session.flush()

        await login_session(client, db_session, email="flt-sort-evt-asc@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?sort=event_count&order=asc")

        assert resp.status_code == 200
        pos_low = resp.text.index("AscLow: 1 event")
        pos_mid = resp.text.index("AscMid: 3 events")
        pos_high = resp.text.index("AscHigh: 5 events")
        assert pos_low < pos_mid < pos_high

    async def test_issue_list_sort_last_seen_asc(self, client, db_session):
        """?sort=last_seen&order=asc -> najstarszy pierwszy."""
        project = _make_project("flt-sort-ls-asc")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        issue_oldest = _make_issue(
            project.id,
            "OldestError: first seen",
            "unresolved",
            last_seen=now - timedelta(days=7),
        )
        issue_middle = _make_issue(
            project.id,
            "MiddleError: middle seen",
            "unresolved",
            last_seen=now - timedelta(days=3),
        )
        issue_newest = _make_issue(
            project.id,
            "NewestError: recent seen",
            "unresolved",
            last_seen=now,
        )
        db_session.add_all([issue_oldest, issue_middle, issue_newest])
        await db_session.flush()

        await login_session(client, db_session, email="flt-sort-ls-asc@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?sort=last_seen&order=asc")

        assert resp.status_code == 200
        pos_oldest = resp.text.index("OldestError: first seen")
        pos_middle = resp.text.index("MiddleError: middle seen")
        pos_newest = resp.text.index("NewestError: recent seen")
        assert pos_oldest < pos_middle < pos_newest

    async def test_issue_list_sort_last_seen_desc_default(self, client, db_session):
        """Domyslne sortowanie: last_seen malejaco (najnowszy pierwszy)."""
        project = _make_project("flt-sort-ls-desc")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        issue_old = _make_issue(
            project.id,
            "OldIssue: 7 days ago",
            "unresolved",
            last_seen=now - timedelta(days=7),
        )
        issue_new = _make_issue(
            project.id,
            "NewIssue: just now",
            "unresolved",
            last_seen=now,
        )
        db_session.add_all([issue_old, issue_new])
        await db_session.flush()

        await login_session(client, db_session, email="flt-sort-ls-desc@test.com")
        # Bez parametrow -> domyslnie last_seen desc
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")

        assert resp.status_code == 200
        pos_new = resp.text.index("NewIssue: just now")
        pos_old = resp.text.index("OldIssue: 7 days ago")
        assert pos_new < pos_old


@pytest.mark.integration
class TestIssueListFallback:
    """Fallback przy nieprawidlowych wartosciach parametrow -- HTTP 200 (nie 500)."""

    async def test_issue_list_invalid_status_fallback(self, client, db_session):
        """?status=garbage -> 200, zachowuje sie jak domyslny (unresolved)."""
        project = _make_project("flt-inv-status")
        db_session.add(project)
        await db_session.flush()

        unresolved = _make_issue(project.id, "UnresolvedError: inv-status", "unresolved")
        resolved = _make_issue(project.id, "ResolvedError: inv-status", "resolved")
        db_session.add_all([unresolved, resolved])
        await db_session.flush()

        await login_session(client, db_session, email="flt-inv-status@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=garbage")

        # Nie moze byc 500 ani 4xx
        assert resp.status_code == 200
        # Fallback do unresolved: nierozwiazany widoczny, resolved nie
        assert "UnresolvedError: inv-status" in resp.text
        assert "ResolvedError: inv-status" not in resp.text

    async def test_issue_list_invalid_sort_fallback(self, client, db_session):
        """?sort=garbage&order=invalid -> 200, nie rzuca bledu."""
        project = _make_project("flt-inv-sort")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, "SomeError: invalid-sort-test", "unresolved")
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="flt-inv-sort@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?sort=garbage&order=invalid")

        assert resp.status_code == 200
        assert "SomeError: invalid-sort-test" in resp.text

    async def test_issue_list_invalid_sort_only_fallback(self, client, db_session):
        """?sort=nonexistent (order poprawny) -> 200."""
        project = _make_project("flt-inv-sort2")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, "SomeError: sort-fallback", "unresolved")
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="flt-inv-sort2@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?sort=nonexistent&order=asc")

        assert resp.status_code == 200

    async def test_issue_list_invalid_order_only_fallback(self, client, db_session):
        """?sort=event_count (poprawny) &order=INVALID -> 200, fallback do desc."""
        project = _make_project("flt-inv-order")
        db_session.add(project)
        await db_session.flush()

        issue_low = _make_issue(project.id, "LowEvt: order-fallback", "unresolved", event_count=1)
        issue_high = _make_issue(project.id, "HighEvt: order-fallback", "unresolved", event_count=10)
        db_session.add_all([issue_low, issue_high])
        await db_session.flush()

        await login_session(client, db_session, email="flt-inv-order@test.com")
        # order=INVALID powinien spasc do "desc"
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?sort=event_count&order=INVALID")

        assert resp.status_code == 200
        # Fallback do desc -> high (10) przed low (1)
        pos_high = resp.text.index("HighEvt: order-fallback")
        pos_low = resp.text.index("LowEvt: order-fallback")
        assert pos_high < pos_low


@pytest.mark.integration
class TestIssueListMembership:
    """Dostep do listy issues wymaga czlonkostwa w projekcie."""

    async def test_issue_list_requires_project_membership(self, client, db_session):
        """User niebedacy czlonkiem projektu -> 403 (nie superuser)."""
        project = _make_project("flt-membership")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, "PrivateError: members only", "unresolved")
        db_session.add(issue)
        await db_session.flush()

        # Tworzymy zwyklego usera (is_superuser=False) bez czlonkostwa
        await login_session(
            client,
            db_session,
            email="flt-membership-nonadmin@test.com",
            is_superuser=False,
        )

        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")

        # require_permission rzuca HTTPException(403) jesli brak czlonkostwa
        assert resp.status_code == 403

    async def test_issue_list_accessible_to_project_member(self, client, db_session):
        """User bedacy czlonkiem projektu (role=member) -> 200."""
        project = _make_project("flt-member-ok")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, "MemberError: visible", "unresolved")
        db_session.add(issue)
        await db_session.flush()

        # Tworzymy zwyklego usera i dodajemy jako czlonek projektu
        user = User(
            email="flt-member-ok@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="member",
        )
        db_session.add(member)
        await db_session.flush()

        # Logujemy sie jako ten user (user juz istnieje, nie uzywamy login_session)
        response = await client.post(
            "/auth/login",
            data={"email": "flt-member-ok@test.com", "password": "testpass123"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "MemberError: visible" in resp.text

    async def test_issue_list_superuser_bypasses_membership(self, client, db_session):
        """Superuser nie musi byc czlonkiem projektu -- dostep 200."""
        project = _make_project("flt-superuser")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, "SuperError: admin visible", "unresolved")
        db_session.add(issue)
        await db_session.flush()

        # login_session tworzy superusera domyslnie
        await login_session(client, db_session, email="flt-superuser@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "SuperError: admin visible" in resp.text


@pytest.mark.integration
class TestIssueListFiltersCombined:
    """Kombinacje filtrow status + sort + order."""

    async def test_issue_list_all_with_sort_event_count_desc(self, client, db_session):
        """?status=all&sort=event_count&order=desc -> wszystkie statusy, po event_count."""
        project = _make_project("flt-comb-all-evt")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        unresolved = _make_issue(
            project.id,
            "Unresolved: 10 events",
            "unresolved",
            event_count=10,
            last_seen=now,
        )
        resolved = _make_issue(
            project.id,
            "Resolved: 5 events",
            "resolved",
            event_count=5,
            last_seen=now,
        )
        ignored = _make_issue(
            project.id,
            "Ignored: 1 event",
            "ignored",
            event_count=1,
            last_seen=now,
        )
        db_session.add_all([unresolved, resolved, ignored])
        await db_session.flush()

        await login_session(client, db_session, email="flt-comb-all-evt@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=all&sort=event_count&order=desc")

        assert resp.status_code == 200
        assert "Unresolved: 10 events" in resp.text
        assert "Resolved: 5 events" in resp.text
        assert "Ignored: 1 event" in resp.text

        # Kolejnosc: 10 > 5 > 1
        pos_unresolved = resp.text.index("Unresolved: 10 events")
        pos_resolved = resp.text.index("Resolved: 5 events")
        pos_ignored = resp.text.index("Ignored: 1 event")
        assert pos_unresolved < pos_resolved < pos_ignored

    async def test_issue_list_resolved_sort_last_seen_asc(self, client, db_session):
        """?status=resolved&sort=last_seen&order=asc -> tylko resolved, od najstarszego."""
        project = _make_project("flt-comb-res-ls")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        resolved_old = _make_issue(
            project.id,
            "OldResolved: long ago",
            "resolved",
            last_seen=now - timedelta(days=10),
        )
        resolved_new = _make_issue(
            project.id,
            "NewResolved: yesterday",
            "resolved",
            last_seen=now - timedelta(days=1),
        )
        unresolved = _make_issue(
            project.id,
            "StillUnresolved: ignore me",
            "unresolved",
            last_seen=now,
        )
        db_session.add_all([resolved_old, resolved_new, unresolved])
        await db_session.flush()

        await login_session(client, db_session, email="flt-comb-res-ls@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues?status=resolved&sort=last_seen&order=asc")

        assert resp.status_code == 200
        assert "OldResolved: long ago" in resp.text
        assert "NewResolved: yesterday" in resp.text
        assert "StillUnresolved: ignore me" not in resp.text

        # Najstarszy pierwszy
        pos_old = resp.text.index("OldResolved: long ago")
        pos_new = resp.text.index("NewResolved: yesterday")
        assert pos_old < pos_new
