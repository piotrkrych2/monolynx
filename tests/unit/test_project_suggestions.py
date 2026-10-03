"""Testy serwisu propozycji wolnego sluga i kodu projektu (MON-244).

`suggest_available_slug` / `suggest_available_code` zwracaja `(dostepne, propozycja)`.
Wolaja baze (fixture `db_session` z rollbackiem), ale bez HTTP.
"""

from __future__ import annotations

import secrets
import uuid

import pytest

from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.services import projects as svc
from monolynx.services.auth import hash_password
from monolynx.services.projects import suggest_available_code, suggest_available_slug

pytestmark = pytest.mark.unit


async def _seed(db, slugs_codes: list[tuple[str, str]], *, is_active: bool = True) -> None:
    for slug, code in slugs_codes:
        db.add(
            Project(
                name="Sugestie projekt",
                slug=slug,
                code=code,
                api_key=secrets.token_urlsafe(32),
                is_active=is_active,
            )
        )
    await db.flush()


async def _seed_slugs(db, slugs: list[str], *, is_active: bool = True) -> None:
    # Kody losowe i unikalne, zeby seed sluga nie zajmowal kodow testowanych obok.
    await _seed(db, [(s, "Z" + secrets.token_hex(4).upper()) for s in slugs], is_active=is_active)


async def _seed_codes(db, codes: list[str], *, is_active: bool = True) -> None:
    await _seed(db, [(f"sug-{uuid.uuid4().hex[:12]}", c) for c in codes], is_active=is_active)


class TestSuggestAvailableSlug:
    async def test_free_slug_returns_true_and_same_value(self, db_session):
        result = await suggest_available_slug(db_session, "wolny-slug-244")

        assert result == (True, "wolny-slug-244")

    async def test_taken_slug_returns_slug_2(self, db_session):
        await _seed_slugs(db_session, ["acme-244"])

        result = await suggest_available_slug(db_session, "acme-244")

        assert result == (False, "acme-244-2")

    async def test_taken_slug_differs_from_free_slug_answer(self, db_session):
        await _seed_slugs(db_session, ["zajety-244"])

        taken = await suggest_available_slug(db_session, "zajety-244")
        free = await suggest_available_slug(db_session, "zajety-244-nie")

        assert taken[0] is False
        assert free[0] is True
        assert taken[1] != "zajety-244"

    async def test_taken_slug_skips_taken_suffixes(self, db_session):
        await _seed_slugs(db_session, ["acme-244", "acme-244-2", "acme-244-3"])

        result = await suggest_available_slug(db_session, "acme-244")

        assert result == (False, "acme-244-4")

    async def test_gap_in_suffixes_returns_first_free(self, db_session):
        # Zajete slug i -3, wolne -2: propozycja to pierwszy wolny, nie pierwszy po ostatnim zajetym.
        await _seed_slugs(db_session, ["gap-244", "gap-244-3"])

        result = await suggest_available_slug(db_session, "gap-244")

        assert result == (False, "gap-244-2")

    async def test_suffix_ten_and_up_is_reachable(self, db_session):
        taken = ["wiele-244"] + [f"wiele-244-{i}" for i in range(2, 10)]
        await _seed_slugs(db_session, taken)

        result = await suggest_available_slug(db_session, "wiele-244")

        assert result == (False, "wiele-244-10")

    async def test_soft_deleted_project_still_occupies_slug(self, db_session):
        await _seed_slugs(db_session, ["usuniety-244"], is_active=False)

        result = await suggest_available_slug(db_session, "usuniety-244")

        assert result == (False, "usuniety-244-2")

    async def test_soft_deleted_suffix_is_skipped(self, db_session):
        await _seed_slugs(db_session, ["miks-244"])
        await _seed_slugs(db_session, ["miks-244-2"], is_active=False)

        result = await suggest_available_slug(db_session, "miks-244")

        assert result == (False, "miks-244-3")

    async def test_no_variant_within_255_chars_returns_none(self, db_session):
        # 254 znaki: kazdy wariant "-N" przekracza limit 255 kolumny, wiec zadnego nie wolno zaproponowac.
        slug = "a" * 254
        await _seed_slugs(db_session, [slug])

        result = await suggest_available_slug(db_session, slug)

        assert result == (False, None)

    async def test_all_99_variants_taken_returns_none(self, db_session):
        base = "pelny-244"
        await _seed_slugs(db_session, [base] + [f"{base}-{i}" for i in range(2, 100)])

        result = await suggest_available_slug(db_session, base)

        assert result == (False, None)

    async def test_suggestion_uses_single_query(self, db_session, monkeypatch):
        await _seed_slugs(db_session, ["jedno-244", "jedno-244-2"])
        original = db_session.execute
        calls: list[object] = []

        async def counting(*args, **kwargs):
            calls.append(args[0])
            return await original(*args, **kwargs)

        monkeypatch.setattr(db_session, "execute", counting)

        result = await suggest_available_slug(db_session, "jedno-244")

        assert result == (False, "jedno-244-3")
        assert len(calls) == 1


class TestSuggestAvailableCode:
    async def test_free_code_returns_true_and_same_value(self, db_session):
        result = await suggest_available_code(db_session, "WOLNY244")

        assert result == (True, "WOLNY244")

    async def test_taken_code_returns_code_2(self, db_session):
        await _seed_codes(db_session, ["ACME"])

        result = await suggest_available_code(db_session, "ACME")

        assert result == (False, "ACME2")

    async def test_taken_code_skips_taken_suffixes(self, db_session):
        await _seed_codes(db_session, ["ACME", "ACME2", "ACME3"])

        result = await suggest_available_code(db_session, "ACME")

        assert result == (False, "ACME4")

    async def test_gap_in_suffixes_returns_first_free(self, db_session):
        await _seed_codes(db_session, ["GAPK", "GAPK3"])

        result = await suggest_available_code(db_session, "GAPK")

        assert result == (False, "GAPK2")

    async def test_ten_char_code_taken_truncates_to_nine_plus_suffix(self, db_session):
        await _seed_codes(db_session, ["ABCDEFGHIJ"])

        result = await suggest_available_code(db_session, "ABCDEFGHIJ")

        assert result == (False, "ABCDEFGHI2")

    async def test_ten_char_code_skips_taken_truncated_variant(self, db_session):
        await _seed_codes(db_session, ["ABCDEFGHIJ", "ABCDEFGHI2"])

        result = await suggest_available_code(db_session, "ABCDEFGHIJ")

        assert result == (False, "ABCDEFGHI3")

    async def test_nine_char_code_taken_gets_ten_char_variant(self, db_session):
        await _seed_codes(db_session, ["ABCDEFGHI"])

        result = await suggest_available_code(db_session, "ABCDEFGHI")

        assert result == (False, "ABCDEFGHI2")

    async def test_two_digit_suffix_truncates_to_eight_chars(self, db_session):
        # Sufiks 10 ma dwie cyfry, wiec z 10-znakowego kodu zostaje 8 znakow + "10".
        taken = ["ABCDEFGHIJ"] + [f"ABCDEFGHI{i}" for i in range(2, 10)]
        await _seed_codes(db_session, taken)

        result = await suggest_available_code(db_session, "ABCDEFGHIJ")

        assert result == (False, "ABCDEFGH10")

    async def test_short_code_reaches_two_digit_suffix(self, db_session):
        await _seed_codes(db_session, ["QZ"] + [f"QZ{i}" for i in range(2, 10)])

        result = await suggest_available_code(db_session, "QZ")

        assert result == (False, "QZ10")

    async def test_soft_deleted_project_still_occupies_code(self, db_session):
        await _seed_codes(db_session, ["USUN"], is_active=False)

        result = await suggest_available_code(db_session, "USUN")

        assert result == (False, "USUN2")

    async def test_soft_deleted_suffix_is_skipped(self, db_session):
        await _seed_codes(db_session, ["MIKS"])
        await _seed_codes(db_session, ["MIKS2"], is_active=False)

        result = await suggest_available_code(db_session, "MIKS")

        assert result == (False, "MIKS3")

    async def test_all_99_variants_taken_returns_none(self, db_session):
        await _seed_codes(db_session, ["PE"] + [f"PE{i}" for i in range(2, 100)])

        result = await suggest_available_code(db_session, "PE")

        assert result == (False, None)

    async def test_suggestion_uses_single_query(self, db_session, monkeypatch):
        await _seed_codes(db_session, ["JEDN", "JEDN2"])
        original = db_session.execute
        calls: list[object] = []

        async def counting(*args, **kwargs):
            calls.append(args[0])
            return await original(*args, **kwargs)

        monkeypatch.setattr(db_session, "execute", counting)

        result = await suggest_available_code(db_session, "JEDN")

        assert result == (False, "JEDN3")
        assert len(calls) == 1


class TestCreateProjectAfterExtraction:
    async def _user(self, db_session, tag: str) -> User:
        user = User(email=f"sugg_{tag}_{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("x"))
        db_session.add(user)
        await db_session.flush()
        return user

    async def test_taken_code_is_replaced_with_free_variant(self, db_session):
        user = await self._user(db_session, "code")
        await _seed_codes(db_session, ["SVCK", "SVCK2"])

        project = await svc.create_project(db_session, user, name="Kod zajety", slug="svc-kod-zajety-244", code="SVCK")

        assert project.code == "SVCK3"
        assert project.slug == "svc-kod-zajety-244"

    async def test_taken_slug_still_raises_value_error(self, db_session):
        user = await self._user(db_session, "slug")
        await _seed_slugs(db_session, ["svc-slug-zajety-244"])

        with pytest.raises(ValueError, match="svc-slug-zajety-244") as exc_info:
            await svc.create_project(db_session, user, name="Slug zajety", slug="svc-slug-zajety-244", code="SVCS")

        assert "juz istnieje" in str(exc_info.value)

    async def test_soft_deleted_slug_still_raises_value_error(self, db_session):
        user = await self._user(db_session, "softslug")
        await _seed_slugs(db_session, ["svc-soft-244"], is_active=False)

        with pytest.raises(ValueError, match="svc-soft-244"):
            await svc.create_project(db_session, user, name="Soft", slug="svc-soft-244", code="SVCF")

    async def test_no_free_code_variant_error_names_original_code(self, db_session):
        user = await self._user(db_session, "nocode")
        await _seed_codes(db_session, ["PE"] + [f"PE{i}" for i in range(2, 100)])

        with pytest.raises(ValueError) as exc_info:
            await svc.create_project(db_session, user, name="Brak kodu", slug="svc-brak-kodu-244", code="PE")

        message = str(exc_info.value)
        assert "'PE'" in message
        assert "None" not in message
