import asyncio
import hashlib
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .seed import SCENARIOS
from .service import DomainError, SecOpsService
from .storage import Store, MissingIncidentError

STATIC = Path(__file__).parent / "static"
ROLES = {"analyst": "安全分析员", "manager": "安全负责人", "operator": "运维执行员"}


class SessionInput(BaseModel):
    role: Literal["analyst", "manager", "operator"]


class IngestInput(BaseModel):
    scenario: Literal["web_attack", "account_anomaly", "authorized_scan", "missing_logs", "blocked_attack"]


class VersionInput(BaseModel):
    version: int = Field(ge=0)


class ReviewInput(VersionInput):
    verdict: Literal["malicious", "authorized", "false_positive", "inconclusive"]
    comment: str = Field(min_length=4, max_length=1000)


class ApprovalInput(VersionInput):
    approved: bool
    comment: str = Field(min_length=2, max_length=1000)


def public_case(case):
    return {key: value for key, value in case.items() if key != "fixture"}


def create_app(db_path=None, step_delay=0.22):
    path = db_path or Path(os.getenv("SECOPS_DATA_DIR", str(Path(__file__).parent.parent / "data"))) / "secops.sqlite3"
    service = SecOpsService(Store(path), step_delay=step_delay)

    @asynccontextmanager
    async def lifespan(app):
        service.seed()
        service.recover_interrupted()
        yield

    app = FastAPI(title="SecOps 安全运营演示", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"])

    @app.middleware("http")
    async def security_headers(request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "拒绝跨站操作请求"}, status_code=403)
            try:
                too_large = int(request.headers.get("content-length", "0")) > 16384
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.code)

    @app.exception_handler(MissingIncidentError)
    async def missing_incident(request, exc):
        return JSONResponse({"detail": "事件不存在"}, status_code=404)

    def actor(request: Request):
        token = request.cookies.get("secops_session", "")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with service.store.connect() as connection:
            row = connection.execute("SELECT role,expires FROM sessions WHERE token=?", (token_hash,)).fetchone()
        if not row or row["expires"] < time.time():
            raise DomainError("请先选择演示角色", 401)
        return row["role"]

    @app.get("/healthz")
    def health():
        return {"status": "ok", "mode": "simulated"}

    @app.post("/api/session")
    def session(payload: SessionInput, request: Request):
        token = secrets.token_urlsafe(32)
        previous = request.cookies.get("secops_session", "")
        with service.store.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires<? OR token=?", (time.time(), hashlib.sha256(previous.encode()).hexdigest()))
            connection.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), payload.role, time.time() + 43200))
        response = JSONResponse({"role": payload.role, "name": ROLES[payload.role], "demo_auth": True})
        response.set_cookie("secops_session", token, httponly=True, samesite="strict", max_age=43200)
        return response

    @app.get("/api/state")
    def state(role=Depends(actor)):
        cases = service.store.list()
        audit = service.store.audit()
        return {"mode": "simulated", "role": role, "role_name": ROLES[role], "incidents": [public_case(c) for c in cases], "audit": audit, "scenarios": [{"id": k, "name": v["name"], "category": v["category"]} for k, v in SCENARIOS.items()], "model_configured": bool(os.getenv("SECOPS_LLM_API_KEY") and os.getenv("SECOPS_LLM_MODEL")), "revision": str(sum(c["version"] for c in cases)) + ":" + str(audit[0]["id"] if audit else 0)}

    @app.get("/api/stream")
    async def stream(request: Request, role=Depends(actor)):
        async def events():
            previous = None
            while not await request.is_disconnected():
                cases = service.store.list()
                audit = service.store.audit()
                revision = str(sum(c["version"] for c in cases)) + ":" + str(audit[0]["id"] if audit else 0)
                if revision != previous:
                    yield "data: " + json.dumps({"revision": revision}) + "\n\n"
                    previous = revision
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.8)
        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/incidents/{incident_id}")
    def detail(incident_id: str, role=Depends(actor)):
        return public_case(service.get(incident_id))

    @app.post("/api/incidents")
    def ingest(payload: IngestInput, role=Depends(actor)):
        if role not in {"analyst", "manager"}:
            raise DomainError("当前角色无权接入场景", 403)
        return public_case(service.ingest(payload.scenario, role))

    @app.post("/api/incidents/{incident_id}/investigate")
    def investigate(incident_id: str, payload: VersionInput, tasks: BackgroundTasks, role=Depends(actor)):
        result = service.start_investigation(incident_id, role, payload.version)
        tasks.add_task(service.run_investigation, incident_id)
        return public_case(result)

    @app.post("/api/incidents/{incident_id}/review")
    def review(incident_id: str, payload: ReviewInput, role=Depends(actor)):
        return public_case(service.review(incident_id, role, payload.version, payload.verdict, payload.comment))

    @app.post("/api/incidents/{incident_id}/approve")
    def approve(incident_id: str, payload: ApprovalInput, role=Depends(actor)):
        return public_case(service.approve(incident_id, role, payload.version, payload.approved, payload.comment))

    @app.post("/api/incidents/{incident_id}/execute")
    def execute(incident_id: str, payload: VersionInput, role=Depends(actor)):
        return public_case(service.execute(incident_id, role, payload.version))

    @app.post("/api/incidents/{incident_id}/reopen-review")
    def reopen_review(incident_id: str, payload: VersionInput, role=Depends(actor)):
        return public_case(service.reopen_review(incident_id, role, payload.version))

    @app.post("/api/incidents/{incident_id}/verify")
    def verify(incident_id: str, payload: VersionInput, role=Depends(actor)):
        return public_case(service.verify(incident_id, role, payload.version))

    @app.get("/api/incidents/{incident_id}/report")
    def report(incident_id: str, role=Depends(actor)):
        case = public_case(service.get(incident_id))
        content = json.dumps({"说明": "模拟业务数据与模拟处置；不是生产安全评估。", "事件": case}, ensure_ascii=False, indent=2)
        return Response(content, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{case["id"]}-report.json"'})

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/docs")
    def api_docs():
        return FileResponse(STATIC / "api.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
