"""Testy integracyjne -- nowy services/wiki_attachments.py (MON-139: wyekstrahowana logika
MCP tooli zalacznikow wiki i globalnych plikow wiki).

Testy wolaja funkcje serwisu bezposrednio z fixture `db_session`, bez przechodzenia przez
wrappery MCP (te maja wlasne testy w tests/unit/test_mcp_server.py, ktorych NIE wolno
zmieniac -- dlatego serwis MUSI umozliwiac wstrzykniecie `upload_object`/`get_object`,
zeby wrapper mogl przekazac swoje globalne `minio_upload_object`/`minio_get_attachment`
patchowane w istniejacych testach). Kontrakt (nazwy, komunikaty, kolejnosc walidacji,
storage_path) wg KONTRAKT API ustalonego przez Team Managera -- patrz tresc ticketu MON-139.

Pokrycie: wspolne walidacje (nazwa pliku, base64, rozmiar, mime_type) i ich kolejnosc,
sanityzacja nazwy pliku, add/list/get zalacznikow strony, add/get/update/list globalnych
plikow, wstrzykiwanie funkcji MinIO (domyslne = funkcje wlasnego modulu).
"""

from __future__ import annotations

import base64
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services import wiki_attachments as svc
from monolynx.services.auth import hash_password

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, tag: str) -> User:
    user = User(email=f"wa_svc_{tag}_{uuid.uuid4().hex[:8]}@x.com", password_hash=hash_password("x"))
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    project = Project(
        name=f"Wiki Attach Svc {tag}",
        slug=f"wa-svc-{tag}-{uuid.uuid4().hex[:6]}",
        code=f"A{secrets.token_hex(3).upper()}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_member(db, project: Project, user: User, role: str = "owner") -> ProjectMember:
    member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db.add(member)
    await db.flush()
    return member


async def _setup(db, tag: str, role: str = "owner"):
    user = await _make_user(db, tag)
    project = await _make_project(db, tag)
    await _make_member(db, project, user, role=role)
    return user, project


async def _add_page(db, project: Project, user: User, **kwargs) -> WikiPage:
    slug = kwargs.pop("slug", f"strona-{uuid.uuid4().hex[:8]}")
    title = kwargs.pop("title", "Strona testowa")
    page = WikiPage(
        id=uuid.uuid4(),
        project_id=project.id,
        parent_id=None,
        title=title,
        slug=slug,
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        created_by_id=user.id,
        last_edited_by_id=user.id,
        **kwargs,
    )
    db.add(page)
    await db.flush()
    return page


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Walidacje wspolne -- add_wiki_page_attachment (komunikaty DOSLOWNIE jak w mcp_server.py)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPageAttachmentValidation:
    async def test_empty_filename_raises(self, db_session):
        user, project = await _setup(db_session, "val-emptyname")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()

        with pytest.raises(ValueError, match=r"^Nazwa pliku nie moze byc pusta$"):
            await svc.add_wiki_page_attachment(
                db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="   ", upload_object=upload_mock
            )
        upload_mock.assert_not_called()

    async def test_dot_filename_raises(self, db_session):
        user, project = await _setup(db_session, "val-dot")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Nieprawidlowa nazwa pliku$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename=".")

    async def test_dotdot_filename_raises(self, db_session):
        user, project = await _setup(db_session, "val-dotdot")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Nieprawidlowa nazwa pliku$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="..")

    async def test_filename_too_long_raises(self, db_session):
        user, project = await _setup(db_session, "val-toolong")
        page = await _add_page(db_session, project, user)
        long_name = "a" * 300 + ".txt"

        with pytest.raises(ValueError, match=r"^Nazwa pliku nie moze przekraczac 255 znakow$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename=long_name)

    async def test_empty_base64_raises(self, db_session):
        user, project = await _setup(db_session, "val-emptyb64")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Zawartosc pliku \(file_base64\) nie moze byc pusta$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64="   ", filename="a.txt")

    async def test_invalid_base64_format_raises(self, db_session):
        user, project = await _setup(db_session, "val-badb64")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Nieprawidlowy format base64$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64="!!!nie-base64!!!", filename="a.txt")

    async def test_file_too_large_raises(self, db_session, monkeypatch):
        monkeypatch.setattr("monolynx.services.wiki_attachments.MAX_WIKI_UPLOAD_SIZE", 4)
        user, project = await _setup(db_session, "val-toolarge")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Plik za duzy: max 200MB, otrzymano \d+\.\dMB$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64=_b64(b"0123456789"), filename="a.txt")

    async def test_invalid_mime_type_raises(self, db_session):
        user, project = await _setup(db_session, "val-badmime")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Nieprawidlowy format mime_type$"):
            await svc.add_wiki_page_attachment(
                db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="a.txt", mime_type="not a mime!"
            )

    async def test_missing_mime_type_defaults_to_octet_stream(self, db_session):
        user, project = await _setup(db_session, "val-defaultmime")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()

        result = await svc.add_wiki_page_attachment(
            db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="a.txt", upload_object=upload_mock
        )

        assert result.mime_type == "application/octet-stream"


@pytest.mark.integration
class TestValidationOrder:
    async def test_filename_validated_before_base64(self, db_session):
        """Nazwa PUSTA i base64 NIEPRAWIDLOWE naraz -- wygrywa blad nazwy (walidacja nazwy pierwsza)."""
        user, project = await _setup(db_session, "order-name-first")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Nazwa pliku nie moze byc pusta$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64="!!!zle!!!", filename="")

    async def test_invalid_base64_checked_before_size(self, db_session, monkeypatch):
        """Base64 NIEPRAWIDLOWY format -- dekodowanie musi paknac PRZED sprawdzeniem rozmiaru,
        wiec nawet z bardzo niskim limitem wynikiem jest blad formatu, nie bledu rozmiaru."""
        monkeypatch.setattr("monolynx.services.wiki_attachments.MAX_WIKI_UPLOAD_SIZE", 1)
        user, project = await _setup(db_session, "order-b64-before-size")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Nieprawidlowy format base64$"):
            await svc.add_wiki_page_attachment(db_session, user, project, str(page.id), file_base64="!!!zle!!!", filename="a.txt")

    async def test_size_validated_before_mime(self, db_session, monkeypatch):
        """Plik za duzy I nieprawidlowy mime naraz -- wygrywa blad rozmiaru (sprawdzany pierwszy)."""
        monkeypatch.setattr("monolynx.services.wiki_attachments.MAX_WIKI_UPLOAD_SIZE", 2)
        user, project = await _setup(db_session, "order-size-before-mime")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Plik za duzy"):
            await svc.add_wiki_page_attachment(
                db_session, user, project, str(page.id), file_base64=_b64(b"0123456789"), filename="a.txt", mime_type="zle!"
            )


@pytest.mark.integration
class TestWikiFileSharedValidation:
    """Podzbior tych samych walidacji na add_wiki_file -- dowod ze logika jest WSPOLNA."""

    async def test_empty_filename_raises(self, db_session):
        user, project = await _setup(db_session, "wf-val-emptyname")
        upload_mock = Mock()

        with pytest.raises(ValueError, match=r"^Nazwa pliku nie moze byc pusta$"):
            await svc.add_wiki_file(db_session, user, project, file_base64=_b64(b"x"), filename="", upload_object=upload_mock)
        upload_mock.assert_not_called()

    async def test_empty_base64_raises(self, db_session):
        user, project = await _setup(db_session, "wf-val-emptyb64")

        with pytest.raises(ValueError, match=r"^Zawartosc pliku \(file_base64\) nie moze byc pusta$"):
            await svc.add_wiki_file(db_session, user, project, file_base64="", filename="a.txt")

    async def test_invalid_base64_format_raises(self, db_session):
        user, project = await _setup(db_session, "wf-val-badb64")

        with pytest.raises(ValueError, match=r"^Nieprawidlowy format base64$"):
            await svc.add_wiki_file(db_session, user, project, file_base64="!!!nie-base64!!!", filename="a.txt")

    async def test_invalid_mime_type_raises(self, db_session):
        user, project = await _setup(db_session, "wf-val-badmime")

        with pytest.raises(ValueError, match=r"^Nieprawidlowy format mime_type$"):
            await svc.add_wiki_file(db_session, user, project, file_base64=_b64(b"x"), filename="a.txt", mime_type="zla!")


# ---------------------------------------------------------------------------
# Sanityzacja nazwy pliku
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFilenameSanitization:
    async def test_path_traversal_reduced_to_basename(self, db_session):
        user, project = await _setup(db_session, "san-traversal")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()

        result = await svc.add_wiki_page_attachment(
            db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="../../etc/passwd", upload_object=upload_mock
        )

        assert result.filename == "passwd"

    async def test_control_chars_and_quotes_replaced_with_underscore(self, db_session):
        user, project = await _setup(db_session, "san-controlchars")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()
        raw_filename = 'a\x01b"c\\d.txt'

        result = await svc.add_wiki_page_attachment(
            db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename=raw_filename, upload_object=upload_mock
        )

        assert result.filename == "a_b_c_d.txt"


# ---------------------------------------------------------------------------
# add_wiki_page_attachment -- zachowanie funkcjonalne
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAddWikiPageAttachment:
    async def test_page_from_other_project_raises_and_upload_not_called(self, db_session):
        user, project = await _setup(db_session, "add-cross")
        other_user, other_project = await _setup(db_session, "add-cross-other")
        other_page = await _add_page(db_session, other_project, other_user)
        upload_mock = Mock()

        with pytest.raises(ValueError, match=r"^Strona wiki nie istnieje$"):
            await svc.add_wiki_page_attachment(
                db_session, user, project, str(other_page.id), file_base64=_b64(b"dane"), filename="a.txt", upload_object=upload_mock
            )
        upload_mock.assert_not_called()

    async def test_storage_path_prefix_extension_and_upload_call(self, db_session):
        user, project = await _setup(db_session, "add-storagepath")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()

        result = await svc.add_wiki_page_attachment(
            db_session, user, project, str(page.id), file_base64=_b64(b"content"), filename="raport.pdf", upload_object=upload_mock
        )

        pattern = rf"^{re.escape(project.slug)}/wiki-attachments/{page.id}/\d{{4}}/\d{{2}}/\d{{2}}/[0-9a-f]{{32}}\.pdf$"
        assert re.match(pattern, result.storage_path), result.storage_path
        upload_mock.assert_called_once_with(result.storage_path, b"content", "application/octet-stream")
        assert result.created_via_ai is True
        assert result.filename == "raport.pdf"

    async def test_filename_without_extension_uses_bin(self, db_session):
        user, project = await _setup(db_session, "add-noext")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()

        result = await svc.add_wiki_page_attachment(
            db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="bezrozszerzenia", upload_object=upload_mock
        )

        assert result.storage_path.endswith(".bin")

    async def test_calls_commit(self, db_session):
        """add_wiki_page_attachment() musi wywolac db.commit() -- test pada na mutancie
        zamieniajacym commit() na flush(): po samym flush() nastepujacy db.refresh() nadal
        dziala (wynik wyglada poprawnie), wiec bez spy na commit() mutant by przezyl
        (.claude/rules/test-kills-mutant.md #7)."""
        user, project = await _setup(db_session, "add-attach-commit")
        page = await _add_page(db_session, project, user)
        upload_mock = Mock()

        with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            result = await svc.add_wiki_page_attachment(
                db_session, user, project, str(page.id), file_base64=_b64(b"x"), filename="a.txt", upload_object=upload_mock
            )

        assert result.id is not None
        commit_spy.assert_awaited()


# ---------------------------------------------------------------------------
# list_wiki_page_attachments
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListWikiPageAttachments:
    async def test_orders_by_created_at_ascending(self, db_session):
        user, project = await _setup(db_session, "list-attach-order")
        page = await _add_page(db_session, project, user)
        now = datetime.now(UTC)
        # Insert NOWSZY jako pierwszy -- lapie brak ORDER BY created_at (asc).
        newer = WikiAttachment(wiki_page_id=page.id, filename="b.txt", storage_path="x/b", size=1, created_at=now)
        older = WikiAttachment(wiki_page_id=page.id, filename="a.txt", storage_path="x/a", size=1, created_at=now - timedelta(minutes=5))
        db_session.add_all([newer, older])
        await db_session.flush()

        result = await svc.list_wiki_page_attachments(db_session, user, project, str(page.id))

        assert [a.filename for a in result] == ["a.txt", "b.txt"]

    async def test_missing_page_raises(self, db_session):
        user, project = await _setup(db_session, "list-attach-missing")

        with pytest.raises(ValueError, match=r"^Strona wiki nie istnieje$"):
            await svc.list_wiki_page_attachments(db_session, user, project, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# get_wiki_attachment
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetWikiAttachment:
    async def test_returns_newest_on_duplicate_filename(self, db_session):
        user, project = await _setup(db_session, "get-attach-dup")
        page = await _add_page(db_session, project, user)
        now = datetime.now(UTC)
        # Insert STARSZY jako pierwszy (kolejnosc INSERT != kolejnosc oczekiwanego wyniku,
        # ktory ma wygrac NAJNOWSZY) -- gdyby serwis brał pierwszy trafiony wiersz bez
        # ORDER BY created_at desc, dostalby "x/old" zamiast "x/new" i test by to zlapal.
        newer = WikiAttachment(wiki_page_id=page.id, filename="dup.txt", storage_path="x/new", size=1, created_at=now)
        older = WikiAttachment(wiki_page_id=page.id, filename="dup.txt", storage_path="x/old", size=1, created_at=now - timedelta(minutes=10))
        db_session.add_all([older, newer])
        await db_session.flush()
        get_mock = Mock(return_value=(b"dane", "text/plain"))

        result = await svc.get_wiki_attachment(db_session, user, project, str(page.id), "dup.txt", get_object=get_mock)

        assert result.attachment.storage_path == "x/new"
        get_mock.assert_called_once_with("x/new")

    async def test_image_content_type_gets_data_uri_prefix(self, db_session):
        user, project = await _setup(db_session, "get-attach-image")
        page = await _add_page(db_session, project, user)
        db_session.add(WikiAttachment(wiki_page_id=page.id, filename="pic.png", storage_path="x/pic", size=4))
        await db_session.flush()
        get_mock = Mock(return_value=(b"\x89PNG", "image/png"))

        result = await svc.get_wiki_attachment(db_session, user, project, str(page.id), "pic.png", get_object=get_mock)

        expected_b64 = base64.b64encode(b"\x89PNG").decode("ascii")
        assert result.content_base64 == f"data:image/png;base64,{expected_b64}"

    async def test_non_image_content_type_has_no_data_uri_prefix(self, db_session):
        user, project = await _setup(db_session, "get-attach-nonimage")
        page = await _add_page(db_session, project, user)
        db_session.add(WikiAttachment(wiki_page_id=page.id, filename="doc.txt", storage_path="x/doc", size=5))
        await db_session.flush()
        get_mock = Mock(return_value=(b"hello", "text/plain"))

        result = await svc.get_wiki_attachment(db_session, user, project, str(page.id), "doc.txt", get_object=get_mock)

        expected_b64 = base64.b64encode(b"hello").decode("ascii")
        assert result.content_base64 == expected_b64
        assert not result.content_base64.startswith("data:")

    async def test_missing_attachment_raises(self, db_session):
        user, project = await _setup(db_session, "get-attach-missing")
        page = await _add_page(db_session, project, user)

        with pytest.raises(ValueError, match=r"^Zalacznik nie istnieje$"):
            await svc.get_wiki_attachment(db_session, user, project, str(page.id), "brak.txt")


# ---------------------------------------------------------------------------
# add_wiki_file
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAddWikiFile:
    async def test_storage_path_prefix_description_and_upload_call(self, db_session):
        user, project = await _setup(db_session, "add-file-ok")
        upload_mock = Mock()

        result = await svc.add_wiki_file(
            db_session, user, project, file_base64=_b64(b"dane"), filename="notatka.md", description="Opis", upload_object=upload_mock
        )

        pattern = rf"^{re.escape(project.slug)}/wiki-files/\d{{4}}/\d{{2}}/\d{{2}}/[0-9a-f]{{32}}\.md$"
        assert re.match(pattern, result.storage_path), result.storage_path
        assert result.description == "Opis"
        assert result.created_via_ai is True
        upload_mock.assert_called_once_with(result.storage_path, b"dane", "application/octet-stream")

    async def test_calls_commit(self, db_session):
        """add_wiki_file() musi wywolac db.commit() -- jak TestAddWikiPageAttachment.test_calls_commit:
        mutant commit()->flush() przezylby bez spy, bo db.refresh() po flush() nadal dziala."""
        user, project = await _setup(db_session, "add-file-commit")
        upload_mock = Mock()

        with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            result = await svc.add_wiki_file(db_session, user, project, file_base64=_b64(b"dane"), filename="a.txt", upload_object=upload_mock)

        assert result.id is not None
        commit_spy.assert_awaited()


# ---------------------------------------------------------------------------
# get_wiki_file
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetWikiFile:
    async def test_other_project_raises(self, db_session):
        user, project = await _setup(db_session, "get-file-cross")
        _other_user, other_project = await _setup(db_session, "get-file-cross-other")
        other_file = WikiFile(project_id=other_project.id, filename="obcy.txt", storage_path="p/obcy", size=1)
        db_session.add(other_file)
        await db_session.flush()

        with pytest.raises(ValueError, match=r"^Plik nie istnieje$"):
            await svc.get_wiki_file(db_session, user, project, str(other_file.id))

    async def test_returns_content_via_injected_get_object(self, db_session):
        user, project = await _setup(db_session, "get-file-ok")
        wiki_file = WikiFile(project_id=project.id, filename="d.txt", storage_path="p/d", size=4)
        db_session.add(wiki_file)
        await db_session.flush()
        get_mock = Mock(return_value=(b"data", "text/plain"))

        result = await svc.get_wiki_file(db_session, user, project, str(wiki_file.id), get_object=get_mock)

        assert result.wiki_file.id == wiki_file.id
        assert result.content_base64 == base64.b64encode(b"data").decode("ascii")
        get_mock.assert_called_once_with("p/d")


# ---------------------------------------------------------------------------
# update_wiki_file
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateWikiFile:
    async def test_missing_file_raises(self, db_session):
        user, project = await _setup(db_session, "upd-file-missing")

        with pytest.raises(ValueError, match=r"^Plik nie istnieje$"):
            await svc.update_wiki_file(db_session, user, project, str(uuid.uuid4()), description="x")

    async def test_empty_description_clears_it(self, db_session):
        user, project = await _setup(db_session, "upd-file-cleardesc")
        wiki_file = WikiFile(project_id=project.id, filename="d.txt", storage_path="p/d", size=1, description="stary opis")
        db_session.add(wiki_file)
        await db_session.flush()

        result = await svc.update_wiki_file(db_session, user, project, str(wiki_file.id), description="")

        assert result.description is None

    async def test_filename_sanitized(self, db_session):
        user, project = await _setup(db_session, "upd-file-sanitize")
        wiki_file = WikiFile(project_id=project.id, filename="orig.txt", storage_path="p/d", size=1)
        db_session.add(wiki_file)
        await db_session.flush()

        result = await svc.update_wiki_file(db_session, user, project, str(wiki_file.id), filename="zla nazwa!@#.txt")

        assert result.filename == "zla_nazwa___.txt"

    async def test_filename_blank_after_sanitization_keeps_original(self, db_session):
        user, project = await _setup(db_session, "upd-file-blank")
        wiki_file = WikiFile(project_id=project.id, filename="original.txt", storage_path="p/d", size=1)
        db_session.add(wiki_file)
        await db_session.flush()

        result = await svc.update_wiki_file(db_session, user, project, str(wiki_file.id), filename="   ")

        assert result.filename == "original.txt"

    async def test_commit_is_visible_after_reread(self, db_session):
        """Jak set_wiki_config -- spy na commit() (rollback fixture nie dowodzi realnego
        commitu z osobnego polaczenia) + ponowny odczyt po expire_all() na wartosc atrybutu."""
        user, project = await _setup(db_session, "upd-file-commit")
        wiki_file = WikiFile(project_id=project.id, filename="d.txt", storage_path="p/d", size=1)
        db_session.add(wiki_file)
        await db_session.flush()
        wiki_file_id = wiki_file.id

        with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            await svc.update_wiki_file(db_session, user, project, str(wiki_file_id), description="nowy opis")

        commit_spy.assert_awaited()
        # wiki_file_id zapamietany PRZED expire_all() -- po expire_all() dostep do atrybutu
        # ORM (w tym .id) wymaga synchronicznego lazy loadu, co w async rzuca MissingGreenlet.
        db_session.expire_all()
        reread = (await db_session.execute(select(WikiFile.description).where(WikiFile.id == wiki_file_id))).scalar_one()
        assert reread == "nowy opis"


# ---------------------------------------------------------------------------
# list_wiki_files
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListWikiFiles:
    async def test_orders_by_created_at_descending(self, db_session):
        user, project = await _setup(db_session, "list-files-order")
        now = datetime.now(UTC)
        # Insert w kolejnosci ROSNACEJ po dacie (older, potem newer) -- oczekiwany wynik to
        # DESC (newer, older), czyli odwrotnosc kolejnosci INSERT -- lapie brak ORDER BY desc.
        older = WikiFile(project_id=project.id, filename="old.txt", storage_path="p1", size=1, created_at=now - timedelta(minutes=10))
        newer = WikiFile(project_id=project.id, filename="new.txt", storage_path="p2", size=1, created_at=now)
        db_session.add_all([older, newer])
        await db_session.flush()

        result = await svc.list_wiki_files(db_session, user, project)

        assert [f.filename for f in result] == ["new.txt", "old.txt"]

    async def test_isolated_by_project(self, db_session):
        user, project = await _setup(db_session, "list-files-iso")
        _other_user, other_project = await _setup(db_session, "list-files-iso-other")
        other_file = WikiFile(project_id=other_project.id, filename="obcy.txt", storage_path="p", size=1)
        db_session.add(other_file)
        await db_session.flush()

        result = await svc.list_wiki_files(db_session, user, project)

        assert result == []


# ---------------------------------------------------------------------------
# Domyslne funkcje MinIO -- gdy upload_object/get_object=None, serwis bierze funkcje z
# WLASNEGO modulu (patchowalne przez monolynx.services.wiki_attachments.upload_object /
# .get_attachment) -- wrapper MCP zawsze przekazuje jawnie swoje globalne nazwy, wiec
# existing testy test_mcp_server.py (patchujace monolynx.mcp_server.minio_*) nie musza
# sie zmienic.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDefaultMinioFunctions:
    @patch("monolynx.services.wiki_attachments.upload_object")
    async def test_add_wiki_file_uses_module_upload_object_when_not_injected(self, mock_upload, db_session):
        mock_upload.return_value = "irrelevant"
        user, project = await _setup(db_session, "default-upload")

        result = await svc.add_wiki_file(db_session, user, project, file_base64=_b64(b"dane"), filename="a.txt")

        mock_upload.assert_called_once_with(result.storage_path, b"dane", "application/octet-stream")

    @patch("monolynx.services.wiki_attachments.get_attachment")
    async def test_get_wiki_file_uses_module_get_attachment_when_not_injected(self, mock_get, db_session):
        mock_get.return_value = (b"data", "text/plain")
        user, project = await _setup(db_session, "default-getattachment")
        wiki_file = WikiFile(project_id=project.id, filename="d.txt", storage_path="stored/path", size=4)
        db_session.add(wiki_file)
        await db_session.flush()

        result = await svc.get_wiki_file(db_session, user, project, str(wiki_file.id))

        mock_get.assert_called_once_with("stored/path")
        assert result.content_base64 == base64.b64encode(b"data").decode("ascii")
