"""Local, read-only HTTP server for the operator workbench.

Built on FastAPI/uvicorn (declared in web-wizard/requirements.txt, never in the
deployer's). It binds exclusively to ``127.0.0.1`` and requires a per-run token,
so nothing else on the machine — or another web page — can reach it.

The workbench edits an in-memory **draft**: every mutation is validated by the
deployer's resolver before it is committed, and nothing is ever written to the
inventory file. The process never imports ``deployment_v3.runner``.
"""

from __future__ import annotations

import argparse
import secrets
import socket
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .loader import REPO_ROOT, LoaderError, list_inventories, load_topology
from .operations import OperationRejected, operations, perform
from .session import DraftSession, SessionError

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
DEFAULT_PORT = 8765
COOKIE_NAME = "wizard_token"


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:  # malformed body
        raise HTTPException(status_code=400, detail="body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return payload


def create_app(token: str) -> FastAPI:
    """Build the ASGI app. ``token`` is the per-run session secret."""
    sessions: dict[str, DraftSession] = {}

    # NB: FastAPI evaluates these annotations at runtime, so they must stay
    # Python 3.9-compatible (typing.Optional, not the ``X | None`` syntax).
    def authorize(request: Request, token_param: Optional[str] = Query(None, alias="token")) -> None:
        supplied = (
            request.headers.get("x-wizard-token")
            or request.cookies.get(COOKIE_NAME)
            or token_param
        )
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=403, detail="missing or invalid session token")

    app = FastAPI(
        title="web-wizard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def no_store_static(request: Request, call_next):
        # The canvas changes often while it is being built; never let a stale
        # script or stylesheet linger in the browser cache.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def _session(session_id: str) -> DraftSession:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown session; open the inventory again")
        return session

    # -- page & assets -------------------------------------------------
    @app.get("/", include_in_schema=False)
    def root(token_param: Optional[str] = Query(None, alias="token")) -> FileResponse:
        if not token_param or not secrets.compare_digest(token_param, token):
            raise HTTPException(
                status_code=403,
                detail="open the URL printed by run.sh (it carries the session token)",
            )
        response = FileResponse(INDEX_HTML, media_type="text/html")
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict")
        return response

    # -- read-only inspection ------------------------------------------
    @app.get("/api/inventories", dependencies=[Depends(authorize)])
    def inventories() -> dict:
        return {"inventories": list_inventories()}

    @app.get("/api/graph", dependencies=[Depends(authorize)])
    def graph(path: str) -> dict:
        try:
            return load_topology(path).to_dict()
        except LoaderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/operations", dependencies=[Depends(authorize)])
    def supported_operations() -> dict:
        return {"operations": operations()}

    # -- draft sessions -------------------------------------------------
    @app.post("/api/session", dependencies=[Depends(authorize)])
    async def open_session(request: Request) -> dict:
        payload = await _json_body(request)
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise HTTPException(status_code=400, detail="body must contain a 'path' string")
        try:
            session = DraftSession.open(path)
        except SessionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session_id = secrets.token_urlsafe(9)
        sessions[session_id] = session
        return {"session_id": session_id, **session.state()}

    @app.get("/api/session/{session_id}", dependencies=[Depends(authorize)])
    def read_session(session_id: str) -> dict:
        return _session(session_id).state()

    @app.post("/api/session/{session_id}/op", dependencies=[Depends(authorize)])
    async def apply_operation(session_id: str, request: Request) -> dict:
        session = _session(session_id)
        payload = await _json_body(request)
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind:
            raise HTTPException(status_code=400, detail="body must contain an operation 'kind'")
        try:
            perform(session, kind, payload.get("params"))
        except OperationRejected as exc:
            # A rejected change is a domain answer, not a transport failure: the
            # draft is untouched and the reason travels with the state.
            state = session.state(ok=False)
            state["error"] = str(exc)
            return state
        return session.state()

    @app.post("/api/session/{session_id}/undo", dependencies=[Depends(authorize)])
    def undo(session_id: str) -> dict:
        session = _session(session_id)
        if not session.undo():
            state = session.state(ok=False)
            state["error"] = "nothing to undo"
            return state
        return session.state()

    @app.post("/api/session/{session_id}/reset", dependencies=[Depends(authorize)])
    def reset(session_id: str) -> dict:
        session = _session(session_id)
        session.reset()
        return session.state()

    @app.post("/api/session/{session_id}/save", dependencies=[Depends(authorize)])
    async def save_session(session_id: str, request: Request) -> dict:
        """Write the draft to a NEW file. The loaded inventory is never touched."""
        session = _session(session_id)
        payload = await _json_body(request)
        destination = payload.get("path")
        if destination is not None and not isinstance(destination, str):
            raise HTTPException(status_code=400, detail="'path' must be a string")
        try:
            written = session.save(destination)
        except SessionError as exc:
            # A refused save is a domain answer, not a transport failure.
            state = session.state(ok=False)
            state["error"] = str(exc)
            return state
        state = session.state()
        state["saved"] = str(written)
        return state

    return app


def _free_port(preferred: int) -> int:
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise SystemExit("could not find a free local port")


def _resolve_inventory_arg(value: Path) -> Path:
    """Resolve a relative --inventory against the caller's CWD, then the repo root."""
    path = value.expanduser()
    if not path.is_absolute():
        candidate = Path.cwd() / path
        path = candidate if candidate.is_file() else REPO_ROOT / path
    return path.resolve()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="web-wizard",
        description="Local, read-only operator workbench for dARK deployment v3",
    )
    parser.add_argument("--inventory", type=Path, help="operator inventory to open")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="127.0.0.1 port (0 = ephemeral)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    token = secrets.token_urlsafe(24)
    port = _free_port(args.port)
    inventory = _resolve_inventory_arg(args.inventory) if args.inventory else None
    url = f"http://127.0.0.1:{port}/?token={token}"
    if inventory:
        url += f"#path={quote(str(inventory))}"

    print("web-wizard — operator workbench")
    print(f"  inventory : {inventory or '(choose from the examples list)'}")
    print(f"  listening : http://127.0.0.1:{port} (127.0.0.1 only)")
    print(f"  open      : {url}")
    print("  stop      : Ctrl+C")
    print("  note      : edits stay in memory; the inventory file is never written")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - best effort
            pass

    uvicorn.run(create_app(token), host="127.0.0.1", port=port, log_level="warning")
    return 0
