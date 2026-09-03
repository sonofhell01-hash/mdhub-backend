from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import ai, auth, checkers, clients, database, documents, evidence, health, hotfix, ingest, midiasimples, noc, operational, sync, technicians, templates, whatsapp
from src.core.config import settings


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Backend central do MD HUB FINAL 2026.",
)

_cors_origins = list(settings.cors_origins)
_cors_wildcard = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Credentials (cookies/Authorization) cannot be combined with a wildcard origin per the
    # CORS spec (browsers ignore allow_credentials when allow_origins=["*"]). Once CORS_ORIGINS
    # is set to the real frontend domain(s) in production, this automatically becomes True.
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ai.router)
app.include_router(checkers.router)
app.include_router(clients.router)
app.include_router(database.router)
app.include_router(documents.router)
app.include_router(evidence.router)
app.include_router(health.router)
app.include_router(hotfix.router)
app.include_router(ingest.router)
app.include_router(midiasimples.router)
app.include_router(noc.router)
app.include_router(operational.router)
app.include_router(sync.router)
app.include_router(technicians.router)
app.include_router(templates.router)
app.include_router(whatsapp.router)


@app.get("/")
def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "status": "ready",
        "docs": "/docs",
        "health": "/health",
    }
