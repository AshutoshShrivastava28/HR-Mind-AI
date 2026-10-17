from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.auth_routes import router as auth_router
from app.api.routes import router as api_router
from app.core.config import BASE_DIR, get_settings
from app.core.logging import configure_logging
from app.services.audit import init_db
from app.services.authentication import init_auth_db
from app.services.conversations import init_conversation_db

configure_logging()

settings = get_settings()

init_db()
init_conversation_db()
init_auth_db()

app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
)

app.include_router(auth_router)
app.include_router(api_router)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
        },
    )


@app.get("/share/{token}", response_class=HTMLResponse)
def shared_conversation_page(request: Request, token: str):
    """Render the standalone read-only page for a shared conversation."""
    if not token or len(token) > 128:
        return HTMLResponse(
            "Shared conversation unavailable.",
            status_code=404,
        )

    return templates.TemplateResponse(
        "shared.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "share_token": token,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
