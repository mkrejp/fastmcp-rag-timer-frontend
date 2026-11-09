"""Google sign-in. Pages and API routes require it. Static files do not."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from rag_frontend.config import Settings

_PUBLIC = {"/health", "/auth/login", "/auth/callback", "/auth/logout"}


def install_auth(app: FastAPI, settings: Settings, forced_user: str | None) -> None:
    """Add the session cookie and the login wall.

    ``forced_user`` is a test seam. Production never sets it, so a request
    without a session cannot reach MCP or the model.
    """
    app.state.forced_user = forced_user
    app.state.allowed_emails = {item.lower() for item in settings.google_allowed_emails}
    app.add_middleware(LoginRequiredMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.public_base_url.startswith("https://"),
        max_age=60 * 60 * 12,
    )
    if settings.google_client_id and settings.google_client_secret:
        from authlib.integrations.starlette_client import OAuth

        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        app.state.oauth = oauth
    else:
        app.state.oauth = None

    @app.get("/auth/login")
    async def login(request: Request) -> Response:
        """Send the browser to Google, or explain that login is not configured."""
        oauth = request.app.state.oauth
        if oauth is None:
            return Response(
                "Google login is not configured.",
                status_code=503,
                media_type="text/plain",
            )
        redirect_uri = settings.public_base_url.rstrip("/") + "/auth/callback"
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @app.get("/auth/callback")
    async def callback(request: Request) -> Response:
        """Store the email when it is on the allowlist."""
        oauth = request.app.state.oauth
        if oauth is None:
            return Response("Google login is not configured.", status_code=503)
        token = await oauth.google.authorize_access_token(request)
        info = token.get("userinfo") or {}
        email = str(info.get("email") or "").strip().lower()
        if email not in request.app.state.allowed_emails:
            return Response("This email is not allowed.", status_code=403)
        request.session["user"] = {"email": email}
        return RedirectResponse("/", status_code=302)

    @app.get("/auth/logout")
    async def logout(request: Request) -> RedirectResponse:
        """Drop the session cookie."""
        request.session.clear()
        return RedirectResponse("/auth/login", status_code=302)


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    """Reject anonymous callers before a route can call MCP or Novita."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        """Allow health, auth, and /static. API routes and the page need a session."""
        path = request.url.path
        if path in _PUBLIC or _is_static(path):
            return await call_next(request)  # type: ignore[operator]
        forced = getattr(request.app.state, "forced_user", None)
        if forced:
            request.session["user"] = {"email": forced}
        user = request.session.get("user")
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Sign in required."}, status_code=401)
            return RedirectResponse("/auth/login", status_code=302)
        email = str(user.get("email") or "").lower()
        allowlist = request.app.state.allowed_emails
        if forced and email == forced:
            return await call_next(request)  # type: ignore[operator]
        if email not in allowlist:
            return JSONResponse(
                {"detail": "This email is not allowed."},
                status_code=403,
            )
        return await call_next(request)  # type: ignore[operator]


def _is_static(path: str) -> bool:
    """Return whether ``path`` is a stylesheet, script, or icon under /static.

    Those files carry no datastore rows. Keeping them behind the login wall
    makes the browser follow a redirect instead of loading the colors.
    """
    return path == "/static" or path.startswith("/static/")
