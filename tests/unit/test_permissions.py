"""Testy jednostkowe/integracyjne serwisu uprawnień RBAC (permissions.py)."""

import secrets
import uuid

import pytest
from fastapi import HTTPException

from monolynx.constants import PERMISSION_ACTIONS, PERMISSION_MODULES
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.services.permissions import (
    check_permission,
    ensure_permission,
    get_user_permissions,
    missing_permissions,
    require_permission,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_project(db, suffix: str) -> Project:
    project = Project(
        name=f"Projekt {suffix}",
        slug=f"projekt-{suffix}",
        code=secrets.token_hex(3).upper(),  # losowy 6-znakowy hex -- unikalny
        api_key=secrets.token_urlsafe(16),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_user(db, email: str, is_superuser: bool = False) -> User:
    user = User(
        email=email,
        password_hash="hashed",
        is_active=True,
        is_superuser=is_superuser,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_role(db, project: Project, permissions: dict) -> Role:
    role = Role(
        name=f"TestRole-{uuid.uuid4().hex[:6]}",
        project_id=project.id,
        permissions=permissions,
    )
    db.add(role)
    await db.flush()
    return role


async def _make_member(
    db,
    project: Project,
    user: User,
    role: Role | None = None,
    role_str: str = "member",
) -> ProjectMember:
    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=role_str,
        role_id=role.id if role else None,
    )
    db.add(member)
    await db.flush()
    return member


# ---------------------------------------------------------------------------
# check_permission
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCheckPermission:
    async def test_superuser_ma_dostep_do_kazdego_modulu(self, db_session):
        """Superuser omija wszystkie sprawdzenia -- zawsze True."""
        project = await _make_project(db_session, "sup1")
        superuser = await _make_user(db_session, "superuser_check@test.com", is_superuser=True)

        for module in PERMISSION_MODULES:
            for action in PERMISSION_ACTIONS:
                result = await check_permission(db_session, superuser.id, project.id, module, action)
                assert result is True, f"Superuser powinien mieć dostęp do {module}:{action}"

    async def test_user_z_rola_owner_ma_pelny_dostep(self, db_session):
        """Rola owner ma read/write/delete dla wszystkich modułów."""
        project = await _make_project(db_session, "own1")
        user = await _make_user(db_session, "owner_check@test.com")
        owner_perms = {m: list(PERMISSION_ACTIONS) for m in PERMISSION_MODULES}
        role = await _make_role(db_session, project, owner_perms)
        await _make_member(db_session, project, user, role=role, role_str="owner")

        for module in PERMISSION_MODULES:
            for action in PERMISSION_ACTIONS:
                result = await check_permission(db_session, user.id, project.id, module, action)
                assert result is True, f"Owner powinien mieć dostęp do {module}:{action}"

    async def test_member_500ki_tylko_read(self, db_session):
        """Rola member ma tylko read dla 500ki -- write i delete są zabronione."""
        project = await _make_project(db_session, "mem1")
        user = await _make_user(db_session, "member_500ki@test.com")
        role = await _make_role(db_session, project, {"500ki": ["read"]})
        await _make_member(db_session, project, user, role=role)

        assert await check_permission(db_session, user.id, project.id, "500ki", "read") is True
        assert await check_permission(db_session, user.id, project.id, "500ki", "write") is False
        assert await check_permission(db_session, user.id, project.id, "500ki", "delete") is False

    async def test_member_scrum_read_i_write(self, db_session):
        """Rola member ma read+write dla scrum, ale nie delete."""
        project = await _make_project(db_session, "mem2")
        user = await _make_user(db_session, "member_scrum@test.com")
        role = await _make_role(db_session, project, {"scrum": ["read", "write"]})
        await _make_member(db_session, project, user, role=role)

        assert await check_permission(db_session, user.id, project.id, "scrum", "read") is True
        assert await check_permission(db_session, user.id, project.id, "scrum", "write") is True
        assert await check_permission(db_session, user.id, project.id, "scrum", "delete") is False

    async def test_user_bez_project_member_zwraca_false(self, db_session):
        """Użytkownik bez rekordu ProjectMember nie ma dostępu."""
        project = await _make_project(db_session, "nomem1")
        user = await _make_user(db_session, "no_member@test.com")

        result = await check_permission(db_session, user.id, project.id, "scrum", "read")
        assert result is False

    async def test_user_z_member_bez_roli_uzywа_legacy_role(self, db_session):
        """ProjectMember bez role_id (role_obj=None) uzywa fallbacku na DEFAULT_ROLE_PERMISSIONS."""
        project = await _make_project(db_session, "norole1")
        user = await _make_user(db_session, "no_role@test.com")
        # Domyslny role_str="member" -- "member" ma "scrum": ["read", "write"] w DEFAULT_ROLE_PERMISSIONS
        await _make_member(db_session, project, user, role=None)

        result = await check_permission(db_session, user.id, project.id, "scrum", "read")
        assert result is True

    async def test_user_z_member_bez_roli_i_bez_legacy_role_zwraca_false(self, db_session):
        """ProjectMember bez role_obj i bez legacy role zwraca False."""
        project = await _make_project(db_session, "norole2")
        user = await _make_user(db_session, "no_role2@test.com")
        # role_str="" -- nie ma pasujacego DEFAULT_ROLE_PERMISSIONS
        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="",  # pusty string -- nie pasuje do zadnej roli
        )
        db_session.add(member)
        await db_session.flush()

        result = await check_permission(db_session, user.id, project.id, "scrum", "read")
        assert result is False

    async def test_nieprawidlowy_modul_zwraca_false(self, db_session):
        """Nieznany moduł natychmiast zwraca False."""
        project = await _make_project(db_session, "invmod1")
        superuser = await _make_user(db_session, "inv_mod@test.com", is_superuser=True)

        result = await check_permission(db_session, superuser.id, project.id, "nieznany_modul", "read")
        assert result is False

    async def test_nieprawidlowa_akcja_zwraca_false(self, db_session):
        """Nieznana akcja natychmiast zwraca False."""
        project = await _make_project(db_session, "invact1")
        superuser = await _make_user(db_session, "inv_action@test.com", is_superuser=True)

        result = await check_permission(db_session, superuser.id, project.id, "scrum", "fly")
        assert result is False

    async def test_nieaktywny_user_zwraca_false(self, db_session):
        """Nieaktywny użytkownik (is_active=False) nie ma dostępu."""
        project = await _make_project(db_session, "inact1")
        user = User(
            email="inactive_user@test.com",
            password_hash="hashed",
            is_active=False,
        )
        db_session.add(user)
        await db_session.flush()

        result = await check_permission(db_session, user.id, project.id, "scrum", "read")
        assert result is False

    async def test_nieistniejacy_user_id_zwraca_false(self, db_session):
        """Nieistniejące user_id zwraca False."""
        project = await _make_project(db_session, "noid1")
        fake_user_id = uuid.uuid4()

        result = await check_permission(db_session, fake_user_id, project.id, "scrum", "read")
        assert result is False

    async def test_brak_uprawnien_dla_modulu_w_roli(self, db_session):
        """Rola bez uprawnień dla danego modułu zwraca False."""
        project = await _make_project(db_session, "nomod1")
        user = await _make_user(db_session, "no_module_perm@test.com")
        # Rola ma uprawnienia tylko dla scrum, brak dla wiki
        role = await _make_role(db_session, project, {"scrum": ["read", "write"]})
        await _make_member(db_session, project, user, role=role)

        result = await check_permission(db_session, user.id, project.id, "wiki", "read")
        assert result is False


# ---------------------------------------------------------------------------
# require_permission
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRequirePermission:
    async def test_rzuca_403_gdy_brak_dostepu(self, db_session):
        """require_permission rzuca HTTPException(403) gdy brak uprawnień."""
        project = await _make_project(db_session, "req1")
        user = await _make_user(db_session, "req_forbidden@test.com")
        # Użytkownik bez ProjectMember → brak dostępu

        with pytest.raises(HTTPException) as exc_info:
            await require_permission(db_session, user.id, project.id, "scrum", "read")

        assert exc_info.value.status_code == 403

    async def test_komunikat_bledu_zawiera_modul_i_akcje(self, db_session):
        """Komunikat HTTPException zawiera nazwę modułu i akcji."""
        project = await _make_project(db_session, "req2")
        user = await _make_user(db_session, "req_msg@test.com")

        with pytest.raises(HTTPException) as exc_info:
            await require_permission(db_session, user.id, project.id, "wiki", "delete")

        assert "wiki" in exc_info.value.detail
        assert "delete" in exc_info.value.detail

    async def test_nie_rzuca_gdy_dostep_jest(self, db_session):
        """require_permission nie rzuca wyjątku gdy użytkownik ma uprawnienie."""
        project = await _make_project(db_session, "req3")
        user = await _make_user(db_session, "req_ok@test.com")
        role = await _make_role(db_session, project, {"scrum": ["read", "write"]})
        await _make_member(db_session, project, user, role=role)

        # Nie powinno rzucić żadnego wyjątku
        await require_permission(db_session, user.id, project.id, "scrum", "read")
        await require_permission(db_session, user.id, project.id, "scrum", "write")

    async def test_superuser_nie_rzuca(self, db_session):
        """Superuser nigdy nie dostaje 403."""
        project = await _make_project(db_session, "req4")
        superuser = await _make_user(db_session, "req_super@test.com", is_superuser=True)

        for module in PERMISSION_MODULES:
            for action in PERMISSION_ACTIONS:
                await require_permission(db_session, superuser.id, project.id, module, action)

    async def test_rzuca_403_dla_nieprawidlowego_modulu(self, db_session):
        """Nieprawidłowy moduł skutkuje HTTPException(403)."""
        project = await _make_project(db_session, "req5")
        superuser = await _make_user(db_session, "req_invmod@test.com", is_superuser=True)

        with pytest.raises(HTTPException) as exc_info:
            await require_permission(db_session, superuser.id, project.id, "invalid", "read")

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# get_user_permissions
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetUserPermissions:
    async def test_superuser_zwraca_pelna_mape(self, db_session):
        """Superuser dostaje pelna mape: wszystkie moduly x wszystkie akcje."""
        project = await _make_project(db_session, "gup1")
        superuser = await _make_user(db_session, "gup_super@test.com", is_superuser=True)

        result = await get_user_permissions(db_session, superuser.id, project.id)

        assert set(result.keys()) == set(PERMISSION_MODULES)
        for module, actions in result.items():
            assert set(actions) == set(PERMISSION_ACTIONS), f"Superuser powinien mieć wszystkie akcje dla {module}"

    async def test_user_z_rola_zwraca_uprawnienia_roli(self, db_session):
        """Użytkownik z rolą dostaje dokładnie uprawnienia tej roli."""
        project = await _make_project(db_session, "gup2")
        user = await _make_user(db_session, "gup_role@test.com")
        expected_perms = {"scrum": ["read", "write"], "wiki": ["read"]}
        role = await _make_role(db_session, project, expected_perms)
        await _make_member(db_session, project, user, role=role)

        result = await get_user_permissions(db_session, user.id, project.id)

        assert result == expected_perms

    async def test_user_bez_member_zwraca_pusty_dict(self, db_session):
        """Użytkownik bez ProjectMember dostaje pusty słownik."""
        project = await _make_project(db_session, "gup3")
        user = await _make_user(db_session, "gup_nomem@test.com")

        result = await get_user_permissions(db_session, user.id, project.id)

        assert result == {}

    async def test_user_z_member_bez_roli_zwraca_default_uprawnienia(self, db_session):
        """ProjectMember bez role_obj (role_id=None) z legacy role zwraca DEFAULT_ROLE_PERMISSIONS."""
        project = await _make_project(db_session, "gup4")
        user = await _make_user(db_session, "gup_norole@test.com")
        # Domyslny role_str="member" -- uzywamy DEFAULT_ROLE_PERMISSIONS["member"]
        await _make_member(db_session, project, user, role=None)

        result = await get_user_permissions(db_session, user.id, project.id)

        # Sprawdz ze wynik jest zgodny z DEFAULT_ROLE_PERMISSIONS["member"]
        assert "scrum" in result
        assert "read" in result["scrum"]
        assert "wiki" in result
        assert result != {}

    async def test_nieistniejacy_user_zwraca_pusty_dict(self, db_session):
        """Nieistniejące user_id zwraca pusty słownik."""
        project = await _make_project(db_session, "gup5")
        fake_user_id = uuid.uuid4()

        result = await get_user_permissions(db_session, fake_user_id, project.id)

        assert result == {}

    async def test_rola_z_pustymi_uprawnieniami(self, db_session):
        """Rola z permissions={} daje pusty słownik uprawnień."""
        project = await _make_project(db_session, "gup6")
        user = await _make_user(db_session, "gup_empty@test.com")
        role = await _make_role(db_session, project, {})
        await _make_member(db_session, project, user, role=role)

        result = await get_user_permissions(db_session, user.id, project.id)

        assert result == {}

    async def test_uprawnienia_dotycza_konkretnego_projektu(self, db_session):
        """Uprawnienia są izolowane per projekt -- inny projekt zwraca {}."""
        project_a = await _make_project(db_session, "gupa")
        project_b = await _make_project(db_session, "gupb")
        user = await _make_user(db_session, "gup_proj_iso@test.com")
        role = await _make_role(db_session, project_a, {"scrum": ["read"]})
        await _make_member(db_session, project_a, user, role=role)

        # Dla projektu A -- ma uprawnienia
        result_a = await get_user_permissions(db_session, user.id, project_a.id)
        assert result_a == {"scrum": ["read"]}

        # Dla projektu B -- brak uprawnień
        result_b = await get_user_permissions(db_session, user.id, project_b.id)
        assert result_b == {}


# ---------------------------------------------------------------------------
# ensure_permission
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEnsurePermission:
    async def test_member_bez_delete_dostaje_value_error_z_pelnym_komunikatem(self, db_session):
        """Legacy rola member nie ma scrum:delete -- ValueError niesie dokladnie "Brak uprawnienia scrum:delete"."""
        project = await _make_project(db_session, "ens1")
        user = await _make_user(db_session, "ensure_member_delete@test.com")
        await _make_member(db_session, project, user, role_str="member")

        with pytest.raises(ValueError, match=r"^Brak uprawnienia scrum:delete$"):
            await ensure_permission(db_session, user.id, project.id, "scrum", "delete")

    async def test_owner_z_delete_nie_rzuca(self, db_session):
        """Legacy rola owner ma scrum:delete i wiki:delete -- helper zwraca None."""
        project = await _make_project(db_session, "ens2")
        user = await _make_user(db_session, "ensure_owner_delete@test.com")
        await _make_member(db_session, project, user, role_str="owner")

        assert await ensure_permission(db_session, user.id, project.id, "scrum", "delete") is None
        assert await ensure_permission(db_session, user.id, project.id, "wiki", "delete") is None

    async def test_komunikat_zawiera_modul_i_akcje_z_wywolania(self, db_session):
        """Tekst zalezy od przekazanego modulu i akcji, a nie jest stalym literalem."""
        project = await _make_project(db_session, "ens3")
        user = await _make_user(db_session, "ensure_msg_wiki@test.com")
        role = await _make_role(db_session, project, {"scrum": ["read"]})
        await _make_member(db_session, project, user, role=role)

        with pytest.raises(ValueError, match=r"^Brak uprawnienia wiki:write$"):
            await ensure_permission(db_session, user.id, project.id, "wiki", "write")

    async def test_rola_niestandardowa_tylko_read_nie_ma_write(self, db_session):
        """role_obj ma pierwszenstwo: rola {scrum: [read]} przepuszcza read i odrzuca write."""
        project = await _make_project(db_session, "ens4")
        user = await _make_user(db_session, "ensure_custom_read@test.com")
        role = await _make_role(db_session, project, {"scrum": ["read"]})
        await _make_member(db_session, project, user, role=role)

        assert await ensure_permission(db_session, user.id, project.id, "scrum", "read") is None
        with pytest.raises(ValueError, match=r"^Brak uprawnienia scrum:write$"):
            await ensure_permission(db_session, user.id, project.id, "scrum", "write")

    async def test_user_spoza_projektu_dostaje_value_error(self, db_session):
        """Brak czlonkostwa to brak uprawnienia, tak samo jak w check_permission."""
        project = await _make_project(db_session, "ens5")
        user = await _make_user(db_session, "ensure_outsider@test.com")

        with pytest.raises(ValueError, match=r"^Brak uprawnienia scrum:read$"):
            await ensure_permission(db_session, user.id, project.id, "scrum", "read")

    async def test_value_error_nie_jest_http_exception(self, db_session):
        """Wariant serwisowy rzuca ValueError (mapowany przez service_error), nie HTTPException jak require_permission."""
        project = await _make_project(db_session, "ens6")
        user = await _make_user(db_session, "ensure_not_http@test.com")

        with pytest.raises(ValueError) as exc_info:
            await ensure_permission(db_session, user.id, project.id, "wiki", "read")

        assert not isinstance(exc_info.value, HTTPException)


# ---------------------------------------------------------------------------
# missing_permissions (MON-243): czysta funkcja "nie nadasz wiecej, niz masz"
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMissingPermissions:
    def test_pusty_requested_nie_ma_brakow(self):
        assert missing_permissions({}, {"scrum": ["read"]}) == []

    def test_pusty_requested_i_pusty_held_nie_maja_brakow(self):
        assert missing_permissions({}, {}) == []

    def test_modul_z_pusta_lista_akcji_nie_jest_brakiem(self):
        assert missing_permissions({"scrum": []}, {}) == []

    def test_identyczne_uprawnienia_nie_maja_brakow(self):
        perms = {"scrum": ["read", "write"], "wiki": ["read"]}

        assert missing_permissions(perms, {"scrum": ["read", "write"], "wiki": ["read"]}) == []

    def test_podzbior_held_nie_ma_brakow(self):
        held = {"scrum": ["read", "write", "delete"], "wiki": ["read", "write"], "settings": ["read"]}

        assert missing_permissions({"scrum": ["write"], "wiki": ["read"]}, held) == []

    def test_brak_modulu_w_held_zwraca_wszystkie_akcje_modulu(self):
        assert missing_permissions({"wiki": ["read", "write"]}, {"scrum": ["read", "write"]}) == ["wiki:read", "wiki:write"]

    def test_brak_akcji_w_istniejacym_module_zwraca_tylko_te_akcje(self):
        assert missing_permissions({"scrum": ["read", "write", "delete"]}, {"scrum": ["read", "write"]}) == ["scrum:delete"]

    def test_akcja_z_innego_modulu_nie_pokrywa_braku(self):
        assert missing_permissions({"wiki": ["delete"]}, {"scrum": ["delete"], "wiki": ["read"]}) == ["wiki:delete"]

    def test_wiele_brakow_jest_posortowanych_niezaleznie_od_kolejnosci_wejscia(self):
        requested = {"wiki": ["write", "delete"], "scrum": ["delete", "read"], "500ki": ["write"]}
        held = {"scrum": ["read"], "wiki": ["delete"]}

        assert missing_permissions(requested, held) == ["500ki:write", "scrum:delete", "wiki:write"]

    def test_przyjmuje_dowolne_iterowalne_zamiast_list(self):
        requested = {"scrum": ("write", "read")}
        held = {"scrum": {"read"}}

        assert missing_permissions(requested, held) == ["scrum:write"]

    def test_held_z_dodatkowymi_modulami_nie_wplywa_na_wynik(self):
        held = {"scrum": ["read"], "wiki": ["read", "write", "delete"], "settings": ["read", "write", "delete"]}

        assert missing_permissions({"scrum": ["read", "write"]}, held) == ["scrum:write"]
