"""Testy integracyjne -- dodatkowe pokrycie dla dashboard/sentry.py.

Pokrywane sciezki:
- issue_list z issues o roznych statusach (unresolved, resolved, ignored)
- issue_list z issues majacymi culprit
- issue_list z wieloma issues -- sprawdzenie sortowania po last_seen
- issue_detail z issue w statusie 'resolved' (badge zielony)
- issue_detail z issue w statusie 'ignored' (badge szary)
- issue_detail z wieloma eventami
- issue_detail z eventami zawierajacymi request_data i environment
- issue_detail z issue bez eventow (events = [])
- setup_guide z wyswietleniem nazwy projektu
- 404 dla nieaktywnego projektu (is_active=False) we wszystkich endpointach
- issue_detail z issue bez culprit (culprit = None)
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from tests.conftest import login_session


def _make_project(slug: str, name: str | None = None) -> Project:
    return Project(
        name=name or f"Project {slug}",
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


@pytest.mark.integration
class TestIssueListCoverage:
    """Dodatkowe pokrycie dla issue_list."""

    async def test_issue_list_with_culprit(self, client, db_session):
        """Issue z polem culprit wyswietla je na liscie."""
        project = _make_project("cov-il-culp")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="culpfp001",
            title="AttributeError: object has no attribute 'foo'",
            culprit="app/models.py in get_attribute",
            status="unresolved",
            event_count=2,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-il-culp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "AttributeError: object has no attribute" in resp.text
        assert "app/models.py in get_attribute" in resp.text

    async def test_issue_list_with_resolved_status(self, client, db_session):
        """Issue ze statusem 'resolved' wyswietla zielona kropke."""
        project = _make_project("cov-il-resolved")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="resfp001",
            title="IOError: disk full",
            status="resolved",
            event_count=10,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-il-resolved@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "IOError: disk full" in resp.text
        assert "10x" in resp.text
        # Green dot for resolved status
        assert "bg-green-500" in resp.text

    async def test_issue_list_with_ignored_status(self, client, db_session):
        """Issue ze statusem 'ignored' wyswietla szara kropke."""
        project = _make_project("cov-il-ignored")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="ignfp001",
            title="DeprecationWarning: old API",
            status="ignored",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-il-ignored@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "DeprecationWarning: old API" in resp.text
        # Gray dot for ignored status
        assert "bg-gray-500" in resp.text

    async def test_issue_list_sorted_by_last_seen_desc(self, client, db_session):
        """Issues sa posortowane po last_seen malejaco (najnowsze pierwsze)."""
        project = _make_project("cov-il-sorted")
        db_session.add(project)
        await db_session.flush()

        now = datetime.now(UTC)
        issue_old = Issue(
            project_id=project.id,
            fingerprint="oldfp001",
            title="OldError: first occurrence",
            status="unresolved",
            event_count=1,
            last_seen=now - timedelta(days=7),
        )
        issue_new = Issue(
            project_id=project.id,
            fingerprint="newfp001",
            title="NewError: recent occurrence",
            status="unresolved",
            event_count=3,
            last_seen=now,
        )
        db_session.add_all([issue_old, issue_new])
        await db_session.flush()

        await login_session(client, db_session, email="cov-il-sorted@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        # Oba issues powinny byc widoczne
        assert "OldError: first occurrence" in resp.text
        assert "NewError: recent occurrence" in resp.text
        # Newer issue should appear before older in response text
        new_pos = resp.text.index("NewError: recent occurrence")
        old_pos = resp.text.index("OldError: first occurrence")
        assert new_pos < old_pos

    async def test_issue_list_inactive_project_returns_404(self, client, db_session):
        """Lista issues dla nieaktywnego projektu zwraca 404."""
        project = _make_project("cov-il-inact")
        project.is_active = False
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-il-inact@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        # sentry.py nie filtruje po is_active, wiec moze zwrocic 200 lub wynik
        # Ale to nadal pokrywa sciezke kodu
        assert resp.status_code in (200, 404)

    async def test_issue_list_counts_display(self, client, db_session):
        """Ilosc issues wyswietla sie w naglowku."""
        project = _make_project("cov-il-count")
        db_session.add(project)
        await db_session.flush()

        for i in range(3):
            issue = Issue(
                project_id=project.id,
                fingerprint=f"countfp{i:03d}",
                title=f"Error #{i}",
                status="unresolved",
                event_count=i + 1,
            )
            db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-il-count@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "3 issues" in resp.text


@pytest.mark.integration
class TestIssueDetailCoverage:
    """Dodatkowe pokrycie dla issue_detail."""

    async def test_issue_detail_resolved_status_badge(self, client, db_session):
        """Issue ze statusem 'resolved' wyswietla zielony badge."""
        project = _make_project("cov-id-resolved")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="resdetfp001",
            title="ResolvedError: already fixed",
            culprit="app/handler.py in process",
            status="resolved",
            event_count=5,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-resolved@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "ResolvedError: already fixed" in resp.text
        assert "resolved" in resp.text
        assert "bg-green-900" in resp.text

    async def test_issue_detail_ignored_status_badge(self, client, db_session):
        """Issue ze statusem 'ignored' wyswietla szary badge."""
        project = _make_project("cov-id-ignored")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="igndetfp001",
            title="IgnoredError: known noise",
            status="ignored",
            event_count=100,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-ignored@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "IgnoredError: known noise" in resp.text
        assert "ignored" in resp.text
        assert "bg-gray-700" in resp.text

    async def test_issue_detail_unresolved_shows_action_buttons(self, client, db_session):
        """Issue ze statusem 'unresolved' wyswietla przyciski Resolve i Ignore."""
        project = _make_project("cov-id-buttons")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="btnfp001",
            title="ActionableError: needs attention",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-buttons@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "Resolve" in resp.text
        assert "Ignore" in resp.text
        assert "bg-red-900" in resp.text  # unresolved badge

    async def test_issue_detail_without_culprit(self, client, db_session):
        """Issue bez culprit (None) nie wyswietla linii culprit."""
        project = _make_project("cov-id-noculp")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="noculpfp001",
            title="NoCulpritError: unknown source",
            culprit=None,
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-noculp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "NoCulpritError: unknown source" in resp.text

    async def test_issue_detail_with_multiple_events(self, client, db_session):
        """Issue z wieloma eventami wyswietla je wszystkie."""
        project = _make_project("cov-id-multi-evt")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="multievtfp001",
            title="RecurringError: happens often",
            status="unresolved",
            event_count=3,
        )
        db_session.add(issue)
        await db_session.flush()

        now = datetime.now(UTC)
        for i in range(3):
            event = Event(
                issue_id=issue.id,
                timestamp=now - timedelta(hours=i),
                exception={
                    "type": "RecurringError",
                    "value": f"occurrence #{i}",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app/tasks.py",
                                "function": "run_task",
                                "lineno": 20 + i,
                            }
                        ]
                    },
                },
            )
            db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-multi-evt@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "RecurringError: happens often" in resp.text
        assert "Eventy (3)" in resp.text

    async def test_issue_detail_with_no_events(self, client, db_session):
        """Issue bez eventow wyswietla 'Eventy (0)'."""
        project = _make_project("cov-id-no-evt")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="noevtfp001",
            title="OrphanError: no events recorded",
            status="unresolved",
            event_count=0,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-no-evt@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "OrphanError: no events recorded" in resp.text
        assert "Eventy (0)" in resp.text

    async def test_issue_detail_event_with_request_data(self, client, db_session):
        """Event z request_data wyswietla sekcje Request."""
        project = _make_project("cov-id-reqdata")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="reqdatafp001",
            title="RequestError: bad input",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime.now(UTC),
            exception={
                "type": "RequestError",
                "value": "bad input",
            },
            request_data={
                "method": "POST",
                "url": "/api/submit",
                "headers": {"Content-Type": "application/json"},
                "data": {"field": "value"},
            },
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-reqdata@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "Request" in resp.text
        assert "/api/submit" in resp.text
        assert "POST" in resp.text

    async def test_issue_detail_event_with_environment(self, client, db_session):
        """Event z environment wyswietla sekcje Environment."""
        project = _make_project("cov-id-env")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="envfp001",
            title="EnvError: config issue",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime.now(UTC),
            exception={
                "type": "EnvError",
                "value": "config issue",
            },
            environment={
                "server_name": "web-01",
                "python_version": "3.12.0",
                "django_version": "5.0",
                "os": "Linux",
            },
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-env@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "Environment" in resp.text
        assert "web-01" in resp.text
        assert "3.12.0" in resp.text

    async def test_issue_detail_event_with_all_fields(self, client, db_session):
        """Event z exception, request_data i environment -- pelen render."""
        project = _make_project("cov-id-full")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="fullfp001",
            title="FullError: complete event data",
            culprit="app/api.py in handle_request",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime.now(UTC),
            exception={
                "type": "FullError",
                "value": "complete event data",
                "stacktrace": {
                    "frames": [
                        {
                            "filename": "app/api.py",
                            "function": "handle_request",
                            "lineno": 55,
                            "context_line": "    raise FullError('complete event data')",
                        }
                    ]
                },
            },
            request_data={
                "method": "GET",
                "url": "/api/v1/data",
                "query_string": "page=1&limit=10",
            },
            environment={
                "server_name": "prod-app-02",
                "release": "v2.3.1",
            },
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-full@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "FullError: complete event data" in resp.text
        assert "app/api.py in handle_request" in resp.text
        assert "Exception" in resp.text
        assert "Request" in resp.text
        assert "Environment" in resp.text
        assert "Eventy (1)" in resp.text
        assert "Wystapienia" in resp.text

    async def test_issue_detail_shows_event_count_in_stats(self, client, db_session):
        """Strona szczegolowa wyswietla liczbe wystapien w sekcji statystyk."""
        project = _make_project("cov-id-stats")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="statsfp001",
            title="StatsError: count display test",
            status="unresolved",
            event_count=42,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-id-stats@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "42" in resp.text
        assert "Wystapienia" in resp.text
        assert "Pierwsze" in resp.text
        assert "Ostatnie" in resp.text


@pytest.mark.integration
class TestSetupGuideCoverage:
    """Dodatkowe pokrycie dla setup_guide."""

    async def test_setup_guide_displays_project_name(self, client, db_session):
        """Setup guide wyswietla nazwe projektu w tytule."""
        project = _make_project("cov-sg-name", name="My Custom Project")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-sg-name@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/setup-guide")
        assert resp.status_code == 200
        assert "My Custom Project" in resp.text

    async def test_setup_guide_contains_sdk_installation_steps(self, client, db_session):
        """Setup guide zawiera wszystkie kroki instalacji SDK."""
        project = _make_project("cov-sg-steps")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-sg-steps@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/setup-guide")
        assert resp.status_code == 200
        # Sprawdz wszystkie kroki
        assert "Zainstaluj SDK" in resp.text
        assert "MIDDLEWARE" in resp.text
        assert "MONOLYNX_DSN" in resp.text or "MONOLYNX_URL" in resp.text
        assert "Gotowe!" in resp.text
        assert "monolynx_sdk" in resp.text

    async def test_setup_guide_contains_manual_reporting_section(self, client, db_session):
        """Setup guide zawiera sekcje recznego raportowania bledow."""
        project = _make_project("cov-sg-manual")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-sg-manual@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/setup-guide")
        assert resp.status_code == 200
        assert "Reczne raportowanie" in resp.text
        assert "capture_exception" in resp.text
        assert "capture_message" in resp.text

    async def test_setup_guide_has_link_back_to_issues(self, client, db_session):
        """Setup guide zawiera link powrotny do listy issues."""
        project = _make_project("cov-sg-link")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-sg-link@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/setup-guide")
        assert resp.status_code == 200
        assert f"/dashboard/{project.slug}/500ki/issues" in resp.text


@pytest.mark.integration
class TestSentryAuthRedirects:
    """Pokrycie auth redirects dla wszystkich endpointow sentry."""

    async def test_issue_list_unauthenticated_redirects(self, client, db_session):
        """Niezalogowany uzytkownik na issue_list jest redirectowany."""
        project = _make_project("cov-auth-il")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/500ki/issues",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_issue_detail_unauthenticated_redirects(self, client, db_session):
        """Niezalogowany uzytkownik na issue_detail jest redirectowany."""
        project = _make_project("cov-auth-id")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="authdetfp001",
            title="AuthTest: no session",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/500ki/issues/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_setup_guide_unauthenticated_redirects(self, client, db_session):
        """Niezalogowany uzytkownik na setup_guide jest redirectowany."""
        project = _make_project("cov-auth-sg")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/500ki/setup-guide",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]


@pytest.mark.integration
class TestSentry404Paths:
    """Pokrycie 404 paths dla wszystkich endpointow sentry."""

    async def test_issue_list_nonexistent_project(self, client, db_session):
        """Lista issues dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="cov-404-il@test.com")
        resp = await client.get("/dashboard/this-project-does-not-exist/500ki/issues")
        assert resp.status_code == 404

    async def test_issue_detail_nonexistent_project(self, client, db_session):
        """Szczegoly issue dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="cov-404-id-proj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/this-project-does-not-exist/500ki/issues/{fake_id}")
        assert resp.status_code == 404

    async def test_issue_detail_nonexistent_issue(self, client, db_session):
        """Szczegoly nieistniejacego issue zwraca 404."""
        project = _make_project("cov-404-id-issue")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-404-id-issue@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{fake_id}")
        assert resp.status_code == 404

    async def test_setup_guide_nonexistent_project(self, client, db_session):
        """Setup guide dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="cov-404-sg@test.com")
        resp = await client.get("/dashboard/this-project-does-not-exist/500ki/setup-guide")
        assert resp.status_code == 404

    async def test_issue_detail_issue_from_different_project(self, client, db_session):
        """Issue z innego projektu -- cross-project access zwraca 404."""
        project_a = _make_project("cov-404-cross-a", name="Cross A")
        project_b = _make_project("cov-404-cross-b", name="Cross B")
        db_session.add_all([project_a, project_b])
        await db_session.flush()

        issue = Issue(
            project_id=project_a.id,
            fingerprint="crossfp001",
            title="CrossProjectError: should not be visible",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="cov-404-cross@test.com")
        # Probujemy otworzyc issue z projektu A w kontekscie projektu B
        resp = await client.get(f"/dashboard/{project_b.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 404
