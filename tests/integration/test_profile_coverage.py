"""Testy integracyjne -- pokrycie brakujacych sciezek w dashboard/profile.py.

Pokrywa endpoint GET /dashboard/profile/skills/download (linie 96-115).
"""

import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import login_session

_EXPECTED_ARC_NAMES = [
    ".claude/skills/monolynx-work/SKILL.md",
    ".claude/skills/monolynx-search/SKILL.md",
    ".claude/skills/monolynx-create-graph-ci-script/SKILL.md",
    ".claude/skills/README.md",
]


@pytest.mark.integration
class TestSkillsDownload:
    async def test_requires_auth(self, client):
        resp = await client.get("/dashboard/profile/skills/download", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_returns_zip(self, client, db_session):
        await login_session(client, db_session, email="skills_dl_1@test.com")
        resp = await client.get("/dashboard/profile/skills/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "monolynx-skills.zip" in resp.headers.get("content-disposition", "")

        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf, "r") as zf:
            assert zf.testzip() is None

    async def test_zip_contains_expected_files(self, client, db_session):
        await login_session(client, db_session, email="skills_dl_2@test.com")
        resp = await client.get("/dashboard/profile/skills/download")
        assert resp.status_code == 200

        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            for expected in _EXPECTED_ARC_NAMES:
                assert expected in names, f"Brak pliku {expected} w ZIP"

    async def test_zip_with_missing_skill_files(self, client, db_session):
        await login_session(client, db_session, email="skills_dl_3@test.com")
        fake_dir = Path("/tmp/nonexistent-skills-dir-xyz")
        with patch("monolynx.dashboard.profile._SKILLS_DIR", fake_dir):
            resp = await client.get("/dashboard/profile/skills/download")

        assert resp.status_code == 200
        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf, "r") as zf:
            assert len(zf.namelist()) == 0
