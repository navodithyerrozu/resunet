"""v1 API router aggregation.

Versioning lives in the URL prefix rather than in a header. Header-based
versioning is arguably cleaner, but a URL version is trivially inspectable in
logs, browser address bars, and curl, which matters far more day to day.

Router order determines OpenAPI tag order, which determines the order sections
appear in the generated docs -- so it is arranged for reading, not
alphabetically.
"""

from fastapi import APIRouter

from app.api.v1.routes import applications, ats, auth, jobs, profile, resumes

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(resumes.router)
api_router.include_router(ats.router)
api_router.include_router(jobs.router)
api_router.include_router(applications.router)

__all__ = ["api_router"]
