"""Testy jednostkowe dla RBAC: model Role, schematy, stale."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from monolynx.constants import (
    ACTION_LABELS,
    DEFAULT_ROLE_PERMISSIONS,
    MODULE_LABELS,
    PERMISSION_ACTIONS,
    PERMISSION_MODULES,
)
from monolynx.models.role import Role
from monolynx.schemas.roles import RoleCreate, RoleResponse, RoleUpdate

# ---------------------------------------------------------------------------
# Stałe RBAC
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPermissionConstants:
    def test_permission_modules_count(self):
        assert len(PERMISSION_MODULES) == 9

    def test_permission_modules_zawiera_oczekiwane(self):
        expected = {
            "500ki",
            "scrum",
            "monitoring",
            "heartbeat",
            "wiki",
            "connections",
            "settings",
            "reports",
            "users",
        }
        assert set(PERMISSION_MODULES) == expected

    def test_permission_actions_count(self):
        assert len(PERMISSION_ACTIONS) == 3

    def test_permission_actions_zawiera_read_write_delete(self):
        assert set(PERMISSION_ACTIONS) == {"read", "write", "delete"}

    def test_module_labels_pokrywa_wszystkie_moduly(self):
        for module in PERMISSION_MODULES:
            assert module in MODULE_LABELS, f"Brak etykiety dla modułu: {module}"

    def test_action_labels_pokrywa_wszystkie_akcje(self):
        for action in PERMISSION_ACTIONS:
            assert action in ACTION_LABELS, f"Brak etykiety dla akcji: {action}"


@pytest.mark.unit
class TestDefaultRolePermissions:
    def test_klucze_owner_admin_member(self):
        assert set(DEFAULT_ROLE_PERMISSIONS.keys()) == {"owner", "admin", "member"}

    def test_owner_ma_pelne_uprawnienia(self):
        owner_perms = DEFAULT_ROLE_PERMISSIONS["owner"]
        for module in PERMISSION_MODULES:
            assert module in owner_perms
            assert set(owner_perms[module]) == set(PERMISSION_ACTIONS)

    def test_member_ma_tylko_read_dla_monitoring(self):
        member_perms = DEFAULT_ROLE_PERMISSIONS["member"]
        assert member_perms["monitoring"] == ["read"]

    def test_wszystkie_akcje_w_domyslnych_uprawnieniach_sa_poprawne(self):
        for role_name, perms in DEFAULT_ROLE_PERMISSIONS.items():
            for module, actions in perms.items():
                assert module in PERMISSION_MODULES, f"Nieznany moduł '{module}' w DEFAULT_ROLE_PERMISSIONS['{role_name}']"
                for action in actions:
                    assert action in PERMISSION_ACTIONS, f"Nieznana akcja '{action}' dla modułu '{module}' w roli '{role_name}'"

    def test_wszystkie_moduły_pokryte_przez_owner(self):
        owner_perms = DEFAULT_ROLE_PERMISSIONS["owner"]
        assert set(owner_perms.keys()) == set(PERMISSION_MODULES)

    def test_member_pokrywa_wszystkie_moduly(self):
        member_perms = DEFAULT_ROLE_PERMISSIONS["member"]
        assert set(member_perms.keys()) == set(PERMISSION_MODULES)


# ---------------------------------------------------------------------------
# Model Role (jednostkowe -- bez bazy)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoleModel:
    def test_tworzenie_z_poprawnymi_danymi(self):
        role_id = uuid.uuid4()
        project_id = uuid.uuid4()
        role = Role(
            id=role_id,
            name="Developer",
            project_id=project_id,
            permissions={"scrum": ["read", "write"]},
            is_system=False,
        )
        assert role.id == role_id
        assert role.name == "Developer"
        assert role.project_id == project_id

    def test_is_system_domyslnie_false(self):
        # SQLAlchemy aplikuje server_default dopiero przy INSERT;
        # Python-level default=False nie jest wymuszony przy samym konstruktorze.
        # Sprawdzamy, że wartość nie jest True (jest None lub False).
        role = Role(
            id=uuid.uuid4(),
            name="Tester",
            permissions={},
        )
        assert not role.is_system

    def test_project_id_moze_byc_none(self):
        role = Role(
            id=uuid.uuid4(),
            name="Global",
            permissions={},
        )
        assert role.project_id is None

    def test_permissions_przechowuje_dict(self):
        perms = {"wiki": ["read", "write", "delete"], "scrum": ["read"]}
        role = Role(
            id=uuid.uuid4(),
            name="Writer",
            permissions=perms,
        )
        assert role.permissions == perms

    def test_permissions_pusty_dict(self):
        role = Role(
            id=uuid.uuid4(),
            name="ReadOnly",
            permissions={},
        )
        assert role.permissions == {}

    def test_nazwa_tablicy(self):
        assert Role.__tablename__ == "roles"

    def test_uuid_primary_key(self):
        role_id = uuid.uuid4()
        role = Role(id=role_id, name="R", permissions={})
        assert role.id == role_id


# ---------------------------------------------------------------------------
# Schemat RoleCreate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoleCreate:
    def test_poprawne_dane(self):
        rc = RoleCreate(
            name="Viewer",
            permissions={"scrum": ["read"], "wiki": ["read"]},
        )
        assert rc.name == "Viewer"
        assert rc.permissions == {"scrum": ["read"], "wiki": ["read"]}

    def test_is_system_domyslnie_false(self):
        rc = RoleCreate(name="X", permissions={})
        assert rc.is_system is False

    def test_project_id_domyslnie_none(self):
        rc = RoleCreate(name="X", permissions={})
        assert rc.project_id is None

    def test_name_max_50_znakow(self):
        with pytest.raises(ValidationError):
            RoleCreate(name="A" * 51, permissions={})

    def test_name_dokladnie_50_znakow_ok(self):
        rc = RoleCreate(name="A" * 50, permissions={})
        assert len(rc.name) == 50

    def test_name_strip_bialych_znakow(self):
        rc = RoleCreate(name="  Admin  ", permissions={})
        assert rc.name == "Admin"

    def test_odrzucenie_nieznanego_modulu(self):
        with pytest.raises(ValidationError):
            RoleCreate(name="X", permissions={"unknown_module": ["read"]})

    def test_odrzucenie_nieznanej_akcji(self):
        with pytest.raises(ValidationError):
            RoleCreate(name="X", permissions={"scrum": ["fly"]})

    def test_akceptacja_wszystkich_znanych_modulow(self):
        perms = {m: ["read"] for m in PERMISSION_MODULES}
        rc = RoleCreate(name="Full", permissions=perms)
        assert set(rc.permissions.keys()) == set(PERMISSION_MODULES)

    def test_akceptacja_wszystkich_znanych_akcji(self):
        rc = RoleCreate(name="X", permissions={"scrum": list(PERMISSION_ACTIONS)})
        assert set(rc.permissions["scrum"]) == set(PERMISSION_ACTIONS)

    def test_puste_permissions_ok(self):
        rc = RoleCreate(name="Empty", permissions={})
        assert rc.permissions == {}

    def test_project_id_ustawiony(self):
        pid = uuid.uuid4()
        rc = RoleCreate(name="X", permissions={}, project_id=pid)
        assert rc.project_id == pid


# ---------------------------------------------------------------------------
# Schemat RoleUpdate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoleUpdate:
    def test_wszystkie_pola_domyslnie_none(self):
        ru = RoleUpdate()
        assert ru.name is None
        assert ru.permissions is None

    def test_tylko_name(self):
        ru = RoleUpdate(name="NewName")
        assert ru.name == "NewName"
        assert ru.permissions is None

    def test_tylko_permissions(self):
        ru = RoleUpdate(permissions={"wiki": ["read"]})
        assert ru.name is None
        assert ru.permissions == {"wiki": ["read"]}

    def test_name_max_50_znakow(self):
        with pytest.raises(ValidationError):
            RoleUpdate(name="B" * 51)

    def test_name_strip_bialych_znakow(self):
        ru = RoleUpdate(name="  Editor  ")
        assert ru.name == "Editor"

    def test_odrzucenie_nieznanego_modulu_w_permissions(self):
        with pytest.raises(ValidationError):
            RoleUpdate(permissions={"nonexistent": ["read"]})

    def test_odrzucenie_nieznanej_akcji_w_permissions(self):
        with pytest.raises(ValidationError):
            RoleUpdate(permissions={"scrum": ["admin"]})

    def test_permissions_none_jest_akceptowane(self):
        ru = RoleUpdate(permissions=None)
        assert ru.permissions is None

    def test_name_none_jest_akceptowane(self):
        ru = RoleUpdate(name=None)
        assert ru.name is None


# ---------------------------------------------------------------------------
# Schemat RoleResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoleResponse:
    def test_from_attributes_dziala(self):
        role_id = uuid.uuid4()
        _project_id = uuid.uuid4()
        now = datetime.now(tz=UTC)

        # Symuluj obiekt ORM-like z atrybutami (użyj type() by uniknąć
        # problemu ze scopingiem w class body wewnątrz funkcji)
        fake_role_cls = type(
            "FakeRole",
            (),
            {
                "id": role_id,
                "name": "Admin",
                "project_id": _project_id,
                "permissions": {"scrum": ["read", "write"]},
                "is_system": True,
                "created_at": now,
                "updated_at": None,
            },
        )

        resp = RoleResponse.model_validate(fake_role_cls())
        assert resp.id == role_id
        assert resp.name == "Admin"
        assert resp.project_id == _project_id
        assert resp.permissions == {"scrum": ["read", "write"]}
        assert resp.is_system is True
        assert resp.created_at == now
        assert resp.updated_at is None

    def test_project_id_moze_byc_none(self):
        role_id = uuid.uuid4()
        now = datetime.now(tz=UTC)

        fake_role_cls = type(
            "FakeRole",
            (),
            {
                "id": role_id,
                "name": "Global",
                "project_id": None,
                "permissions": {},
                "is_system": False,
                "created_at": now,
                "updated_at": None,
            },
        )

        resp = RoleResponse.model_validate(fake_role_cls())
        assert resp.project_id is None

    def test_updated_at_moze_byc_datetime(self):
        role_id = uuid.uuid4()
        now = datetime.now(tz=UTC)

        fake_role_cls = type(
            "FakeRole",
            (),
            {
                "id": role_id,
                "name": "Modified",
                "project_id": None,
                "permissions": {},
                "is_system": False,
                "created_at": now,
                "updated_at": now,
            },
        )

        resp = RoleResponse.model_validate(fake_role_cls())
        assert resp.updated_at == now
