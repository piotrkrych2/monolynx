"""Dashboard -- routery modulowe."""

from fastapi import APIRouter

from .auth import router as auth_router
from .connections import router as connections_router
from .monitoring import router as monitoring_router
from .profile import router as profile_router
from .projects import router as projects_router
from .reports import router as reports_router
from .scrum import router as scrum_router
from .sentry import router as sentry_router
from .settings import router as settings_router
from .users import router as users_router
from .wiki import router as wiki_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(projects_router)
# Statyczne route'y (users, settings, profile)
# przed dynamicznymi (sentry/scrum z {slug})
router.include_router(profile_router)
router.include_router(users_router)
router.include_router(reports_router)
router.include_router(settings_router)
router.include_router(scrum_router)
router.include_router(sentry_router)
router.include_router(monitoring_router)
router.include_router(wiki_router)
router.include_router(connections_router)
