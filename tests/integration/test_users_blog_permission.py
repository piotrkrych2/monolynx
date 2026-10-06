"""Testy integracyjne (MON-270, K5): nadawanie i odbieranie uprawnienia do bloga na koncie użytkownika.

`POST /dashboard/users/{user_id}/blog-permission` z polem formularza `enabled` (`true` / `false`)
ustawia `User.can_edit_blog`. Robi to wyłącznie superuser; właściciel i admin projektu oraz
zwykły członek dostają 403, a flaga zostaje bez zmian. Asercje dotyczą statusu i stanu bazy,
nie adresu przekierowania ani treści flasha.
"""

import secrets
import uuid

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.auth import hash_password

pytestmark = pytest.mark.integration


async def _login(client, db_session, email, *, is_superuser, project=None, role=None):
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=is_superuser)
    db_session.add(user)
    await db_session.flush()
    if role is not None:
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        await db_session.flush()
    resp = await client.post("/auth/login", data={"email": email, "password": "testpass123"}, follow_redirects=False)
    assert resp.status_code == 303
    return user


async def _target(db_session, tag, *, can_edit_blog=False):
    target = User(email=f"blogperm-target-{tag}@test.com", password_hash=hash_password("testpass123"), can_edit_blog=can_edit_blog)
    db_session.add(target)
    await db_session.flush()
    return target.id


async def _project(db_session, tag):
    project = Project(name=f"Blogperm {tag}", slug=f"blogperm-{tag}", code="B" + secrets.token_hex(3).upper(), api_key=secrets.token_urlsafe(32))
    db_session.add(project)
    await db_session.flush()
    return project


async def _flag(db_session, user_id):
    return (await db_session.execute(select(User.can_edit_blog).where(User.id == user_id))).scalar_one()


async def _post(client, user_id, enabled):
    return await client.post(f"/dashboard/users/{user_id}/blog-permission", data={"enabled": enabled}, follow_redirects=False)


class TestSuperuserTogglesPermission:
    async def test_enabling_sets_flag_and_redirects(self, client, db_session):
        await _login(client, db_session, "blogperm-su-on@test.com", is_superuser=True)
        target_id = await _target(db_session, "on")

        resp = await _post(client, target_id, "true")

        assert resp.status_code == 303
        assert await _flag(db_session, target_id) is True

    async def test_disabling_clears_flag_and_redirects(self, client, db_session):
        await _login(client, db_session, "blogperm-su-off@test.com", is_superuser=True)
        target_id = await _target(db_session, "off", can_edit_blog=True)

        resp = await _post(client, target_id, "false")

        assert resp.status_code == 303
        assert await _flag(db_session, target_id) is False

    async def test_enabling_twice_keeps_flag_set(self, client, db_session):
        await _login(client, db_session, "blogperm-su-twice-on@test.com", is_superuser=True)
        target_id = await _target(db_session, "twice-on")

        first = await _post(client, target_id, "true")
        second = await _post(client, target_id, "true")

        assert (first.status_code, second.status_code) == (303, 303)
        assert await _flag(db_session, target_id) is True

    async def test_disabling_twice_keeps_flag_cleared(self, client, db_session):
        await _login(client, db_session, "blogperm-su-twice-off@test.com", is_superuser=True)
        target_id = await _target(db_session, "twice-off", can_edit_blog=True)

        first = await _post(client, target_id, "false")
        second = await _post(client, target_id, "false")

        assert (first.status_code, second.status_code) == (303, 303)
        assert await _flag(db_session, target_id) is False

    async def test_enable_then_disable_round_trip(self, client, db_session):
        await _login(client, db_session, "blogperm-su-round@test.com", is_superuser=True)
        target_id = await _target(db_session, "round")

        await _post(client, target_id, "true")
        assert await _flag(db_session, target_id) is True
        await _post(client, target_id, "false")

        assert await _flag(db_session, target_id) is False

    async def test_change_is_committed(self, client, db_session, monkeypatch):
        await _login(client, db_session, "blogperm-su-commit@test.com", is_superuser=True)
        target_id = await _target(db_session, "commit")
        commits: list[bool] = []
        original = db_session.commit

        async def spy_commit():
            commits.append(await _flag(db_session, target_id))
            await original()

        monkeypatch.setattr(db_session, "commit", spy_commit)

        resp = await _post(client, target_id, "true")

        assert resp.status_code == 303
        assert True in commits

    async def test_toggle_affects_only_the_target_account(self, client, db_session):
        acting = await _login(client, db_session, "blogperm-su-only@test.com", is_superuser=True)
        target_id = await _target(db_session, "only")
        bystander_id = await _target(db_session, "bystander")

        await _post(client, target_id, "true")

        assert await _flag(db_session, target_id) is True
        assert await _flag(db_session, bystander_id) is False
        assert await _flag(db_session, acting.id) is False


class TestNonSuperuserIsRefused:
    @pytest.mark.parametrize("role", ["owner", "admin", "member"])
    async def test_project_role_cannot_enable_permission(self, client, db_session, role):
        project = await _project(db_session, f"deny-on-{role}")
        await _login(client, db_session, f"blogperm-{role}-on@test.com", is_superuser=False, project=project, role=role)
        target_id = await _target(db_session, f"deny-on-{role}")

        resp = await _post(client, target_id, "true")

        assert resp.status_code == 403
        assert await _flag(db_session, target_id) is False

    @pytest.mark.parametrize("role", ["owner", "admin", "member"])
    async def test_project_role_cannot_disable_permission(self, client, db_session, role):
        project = await _project(db_session, f"deny-off-{role}")
        await _login(client, db_session, f"blogperm-{role}-off@test.com", is_superuser=False, project=project, role=role)
        target_id = await _target(db_session, f"deny-off-{role}", can_edit_blog=True)

        resp = await _post(client, target_id, "false")

        assert resp.status_code == 403
        assert await _flag(db_session, target_id) is True

    async def test_account_with_flag_cannot_grant_it_to_others(self, client, db_session):
        acting = await _login(client, db_session, "blogperm-flagged@test.com", is_superuser=False)
        acting.can_edit_blog = True
        await db_session.flush()
        target_id = await _target(db_session, "flagged")

        resp = await _post(client, target_id, "true")

        assert resp.status_code == 403
        assert await _flag(db_session, target_id) is False

    async def test_account_cannot_grant_itself_the_permission(self, client, db_session):
        project = await _project(db_session, "self")
        acting = await _login(client, db_session, "blogperm-self@test.com", is_superuser=False, project=project, role="owner")

        resp = await _post(client, acting.id, "true")

        assert resp.status_code == 403
        assert await _flag(db_session, acting.id) is False

    async def test_anonymous_is_redirected_to_login_and_flag_unchanged(self, client, db_session):
        target_id = await _target(db_session, "anon")

        resp = await _post(client, target_id, "true")

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]
        assert await _flag(db_session, target_id) is False


class TestUnknownTarget:
    async def test_unknown_account_returns_404(self, client, db_session):
        await _login(client, db_session, "blogperm-su-404@test.com", is_superuser=True)

        resp = await _post(client, uuid.uuid4(), "true")

        assert resp.status_code == 404


class TestEnabledFieldParsing:
    async def test_uppercase_true_enables(self, client, db_session):
        await _login(client, db_session, "blogperm-su-upper@test.com", is_superuser=True)
        target_id = await _target(db_session, "upper")

        resp = await _post(client, target_id, "TRUE")

        assert resp.status_code == 303
        assert await _flag(db_session, target_id) is True

    @pytest.mark.parametrize("value", ["1", "yes", "on", "tak"])
    async def test_any_value_other_than_true_clears_the_flag(self, client, db_session, value):
        await _login(client, db_session, f"blogperm-su-other-{value}@test.com", is_superuser=True)
        target_id = await _target(db_session, f"other-{value}", can_edit_blog=True)

        resp = await _post(client, target_id, value)

        assert resp.status_code == 303
        assert await _flag(db_session, target_id) is False

    async def test_missing_field_clears_the_flag_and_never_grants_it(self, client, db_session):
        await _login(client, db_session, "blogperm-su-missing@test.com", is_superuser=True)
        granted_id = await _target(db_session, "missing-granted", can_edit_blog=True)
        plain_id = await _target(db_session, "missing-plain")

        first = await client.post(f"/dashboard/users/{granted_id}/blog-permission", follow_redirects=False)
        second = await client.post(f"/dashboard/users/{plain_id}/blog-permission", follow_redirects=False)

        assert (first.status_code, second.status_code) == (303, 303)
        assert await _flag(db_session, granted_id) is False
        assert await _flag(db_session, plain_id) is False


class TestInactiveTarget:
    async def test_inactive_account_returns_404_and_flag_stays(self, client, db_session):
        await _login(client, db_session, "blogperm-su-inactive@test.com", is_superuser=True)
        target = User(email="blogperm-target-inactive@test.com", password_hash=hash_password("testpass123"), is_active=False)
        db_session.add(target)
        await db_session.flush()
        target_id = target.id

        resp = await _post(client, target_id, "true")

        assert resp.status_code == 404
        assert await _flag(db_session, target_id) is False


class TestUserPagesShowBlogPermission:
    async def test_detail_of_account_without_flag_offers_to_grant(self, client, db_session):
        await _login(client, db_session, "blogperm-su-detail-plain@test.com", is_superuser=True)
        target_id = await _target(db_session, "detail-plain")

        resp = await client.get(f"/dashboard/users/{target_id}")

        assert resp.status_code == 200
        assert f'action="/dashboard/users/{target_id}/blog-permission"' in resp.text
        assert 'name="enabled" value="true"' in resp.text
        assert "Nadaj uprawnienie" in resp.text
        assert "Odbierz uprawnienie" not in resp.text

    async def test_detail_of_account_with_flag_offers_to_revoke(self, client, db_session):
        await _login(client, db_session, "blogperm-su-detail-flag@test.com", is_superuser=True)
        target_id = await _target(db_session, "detail-flag", can_edit_blog=True)

        resp = await client.get(f"/dashboard/users/{target_id}")

        assert resp.status_code == 200
        assert 'name="enabled" value="false"' in resp.text
        assert "Odbierz uprawnienie" in resp.text
        assert "Nadaj uprawnienie" not in resp.text

    async def test_detail_of_superuser_has_no_toggle_form(self, client, db_session):
        admin = await _login(client, db_session, "blogperm-su-detail-super@test.com", is_superuser=True)

        resp = await client.get(f"/dashboard/users/{admin.id}")

        assert resp.status_code == 200
        assert "Superużytkownik ma uprawnienie do edycji bloga zawsze." in resp.text
        assert "/blog-permission" not in resp.text
        assert "Nadaj uprawnienie" not in resp.text

    async def test_list_marks_only_non_superuser_accounts_with_flag(self, client, db_session):
        await _login(client, db_session, "blogperm-su-list@test.com", is_superuser=True)
        flagged_id = await _target(db_session, "list-flag", can_edit_blog=True)
        plain_id = await _target(db_session, "list-plain")
        flagged_super = User(
            email="blogperm-list-flagged-super@test.com", password_hash=hash_password("testpass123"), is_superuser=True, can_edit_blog=True
        )
        db_session.add(flagged_super)
        await db_session.flush()
        assert plain_id != flagged_id

        html = (await client.get("/dashboard/users")).text

        def row(email):
            return next(chunk for chunk in html.split("<tr") if email in chunk)

        assert "bg-emerald-600/30" in row("blogperm-target-list-flag@test.com")
        assert "bg-emerald-600/30" not in row("blogperm-target-list-plain@test.com")
        assert "bg-emerald-600/30" not in row("blogperm-list-flagged-super@test.com")
