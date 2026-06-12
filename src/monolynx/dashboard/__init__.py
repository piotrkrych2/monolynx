"""Dashboard -- routery modulowe."""

from fastapi import APIRouter

from .auth import router as auth_router
from .connections import router as connections_router
from .heartbeat import router as heartbeat_router
from .monitoring import router as monitoring_router
from .pipelines import router as pipelines_router
from .profile import router as profile_router
from .projects import router as projects_router
from .reports import router as reports_router
from .scrum import router as scrum_router
from .sentry import router as sentry_router
from .settings import router as settings_router
from .settlements import router as settlements_router
from .settlements_global import router as settlements_global_router
from .users import router as users_router
from .wiki import router as wiki_router
from .work_plan import router as work_plan_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(projects_router)
# Statyczne route'y (users, settings, profile)
# przed dynamicznymi (sentry/scrum z {slug})
router.include_router(profile_router)
router.include_router(users_router)
router.include_router(reports_router)
router.include_router(work_plan_router)
router.include_router(settlements_global_router)
router.include_router(settings_router)
router.include_router(scrum_router)
router.include_router(sentry_router)
router.include_router(monitoring_router)
router.include_router(heartbeat_router)
router.include_router(wiki_router)
router.include_router(connections_router)
router.include_router(pipelines_router)
router.include_router(settlements_router)
