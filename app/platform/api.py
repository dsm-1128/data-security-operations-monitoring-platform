import asyncio
import copy
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
import redis
from sqlalchemy import select, text

from app.seed import SCENARIOS, make_incident, now
from .auth import authorize
from .config import Settings, config_fingerprint
from .database import Database, audit
from .execution import ActionService
from .runs import Conflict, RunStore
from .runtime import connector_for, process_run
from .sessions import SessionService
from .evidence import EvidenceArchive
from .telemetry import persisted_metrics


STATIC = Path(__file__).resolve().parents[1] / 'static'


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AlertInput(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    category: Literal['web', 'identity']
    asset: str = Field(min_length=1, max_length=200)
    ip: str = Field(default='', max_length=64)
    account: str | None = Field(default=None, max_length=200)
    path: str = Field(default='', max_length=500)
    event_time: AwareDatetime | None = None


class RunInput(StrictInput):
    scenario: str | None = None
    alert: AlertInput | None = None


class DemoInput(StrictInput):
    role: Literal['analyst', 'manager', 'operator', 'auditor', 'admin']


class CommentInput(StrictInput):
    comment: str = Field(min_length=4, max_length=2000)


class ReviewInput(CommentInput):
    verdict: Literal['malicious', 'authorized', 'false_positive', 'inconclusive']


class ApproveInput(StrictInput):
    approved: bool
    comment: str = Field(min_length=2, max_length=1000)


class ActionInput(StrictInput):
    kind: Literal['block_ip', 'freeze_account']
    target: str = Field(min_length=1, max_length=200)
    duration_minutes: int = Field(ge=1, le=60)
    reason: str = Field(min_length=4, max_length=1000)


def public_run(state):
    result = copy.deepcopy(state)
    for key in ('messages', 'pending', 'query_cache', 'principal'):
        result.pop(key, None)
    if 'multi_agent' in result:
        # Match the existing public/private boundary for child message contexts.
        result['multi_agent'].pop('subagent_states', None)
    result['case'].pop('fixture', None)
    if result['mode'] == 'simulated' and result.get('report'):
        result['report'].update(model_used=False, engine=('离线模拟策略 / Supervisor + SubAgents 测试模式'
                                if 'multi_agent' in result else '离线模拟策略 / Agent Loop 测试模式'))
    return result


def create_platform_app(settings=None, model_factory=None, registry_factory=None):
    settings = settings or Settings.from_env()
    settings.validate_startup()
    if settings.database_url.startswith('sqlite:///') and ':memory:' not in settings.database_url:
        Path(settings.database_url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url, initialize=settings.mode == 'development')
    store, sessions = RunStore(db), SessionService(db, settings)
    action_service = ActionService(store, connector_for(settings), settings.resolve_principal)

    @asynccontextmanager
    async def lifespan(app):
        yield
        db.close()

    app = FastAPI(title='SecOps Agent Loop Platform', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.settings = store, settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts.split(','))

    @app.middleware('http')
    async def security(request, call_next):
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            origin = request.headers.get('origin')
            expected = urlsplit(settings.public_url).netloc if settings.mode == 'production' else request.headers.get('host')
            if (origin and urlsplit(origin).netloc != expected) or (settings.mode == 'production' and not origin):
                return JSONResponse({'detail': '拒绝跨站或缺少来源的操作'}, status_code=403)
            body, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > 65536:
                    return JSONResponse({'detail': '请求过大'}, status_code=413)
                body.append(chunk)
            request._body = b''.join(body)
        response = await call_next(request)
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Cache-Control': 'no-store'})
        return response

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=403)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': '记录不存在'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': '输入或配置不符合约束'}, status_code=422)

    def principal(request: Request):
        try:
            return sessions.resolve(request.cookies.get('secops_platform', ''))
        except PermissionError:
            raise HTTPException(401, '请登录或身份已失效')

    def set_session(response, token):
        response.set_cookie('secops_platform', token, secure=settings.mode == 'production', httponly=True, samesite='lax', max_age=3600)
        return response

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': settings.mode}

    @app.get('/readyz')
    def readiness():
        try:
            with db.engine.connect() as connection:
                connection.execute(select(audit.c.id).limit(1))
            if settings.dispatch == 'celery':
                client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
                if not client.ping() or not client.exists('secops:worker:heartbeat'):
                    return JSONResponse({'status': 'not_ready', 'reason': 'worker_or_broker_unavailable'}, status_code=503)
            return {'status': 'ready', 'production': settings.mode == 'production'}
        except Exception:
            return JSONResponse({'status': 'not_ready', 'reason': 'dependency_unavailable'}, status_code=503)

    @app.get('/api/platform/config')
    def config():
        return {'environment': settings.mode, 'model_mode': settings.model_mode, 'connector_mode': settings.connector_mode,
            'sso': bool(settings.oidc_issuer), 'demo_login': settings.mode == 'development'}

    @app.post('/api/platform/session/demo')
    def demo(payload: DemoInput, request: Request):
        if settings.mode != 'development':
            raise PermissionError('production 禁止演示身份')
        sessions.revoke(request.cookies.get('secops_platform', ''))
        return set_session(JSONResponse({'mode': 'development'}), sessions.issue('demo-' + payload.role))

    @app.get('/auth/login')
    def oidc_login():
        start = sessions.start_oidc()
        response = RedirectResponse(start['url'])
        response.set_cookie('secops_oidc', start['binding'], httponly=True, secure=settings.mode == 'production', samesite='lax', max_age=300)
        return response

    @app.get('/auth/callback')
    def oidc_callback(request: Request, state: str, code: str):
        token = sessions.finish_oidc(state, code, request.cookies.get('secops_oidc', ''))
        response = set_session(RedirectResponse('/'), token)
        response.delete_cookie('secops_oidc')
        return response

    @app.get('/api/platform/me')
    def me(actor=Depends(principal)):
        return actor.as_dict()

    @app.post('/api/platform/logout')
    def logout(request: Request):
        sessions.revoke(request.cookies.get('secops_platform', ''))
        response = JSONResponse({'ok': True})
        response.delete_cookie('secops_platform')
        return response

    @app.get('/api/platform/scenarios')
    def scenarios(actor=Depends(principal)):
        return [{'id': k, 'name': v['name']} for k, v in SCENARIOS.items()] if settings.connector_mode == 'simulated' else []

    @app.post('/api/platform/runs', status_code=201)
    def create_run(payload: RunInput, tasks: BackgroundTasks, actor=Depends(principal)):
        if bool(payload.scenario) == bool(payload.alert):
            raise ValueError('选择一个告警来源')
        if payload.scenario:
            if settings.connector_mode != 'simulated' or payload.scenario not in SCENARIOS:
                raise PermissionError('当前模式不允许模拟告警')
            case = make_incident(payload.scenario)
        else:
            if settings.connector_mode == 'simulated':
                raise ValueError('真实告警需要真实连接器')
            alert = payload.alert.model_dump(mode='json')
            case = {**alert, 'id': 'SEC-' + uuid.uuid4().hex, 'created_at': alert.get('event_time') or now(), 'simulated': False}
        mode = 'simulated' if settings.model_mode == 'simulated' else ('live_model_simulated_tools' if settings.connector_mode == 'simulated' else 'live')
        state = store.create(case, actor, mode, config_fingerprint=config_fingerprint(settings), **settings.budgets())
        if settings.dispatch == 'inline':
            tasks.add_task(process_run, store, settings, state['id'], model_factory, registry_factory)
        return public_run(state)

    @app.get('/api/platform/runs')
    def list_runs(actor=Depends(principal)):
        return [public_run(s) for s in store.list_for(actor)]

    @app.get('/api/platform/runs/{run_id}')
    def run_detail(run_id: str, actor=Depends(principal)):
        return public_run(store.get(run_id, actor))

    @app.post('/api/platform/runs/{run_id}/cancel')
    def cancel(run_id: str, actor=Depends(principal)):
        return public_run(store.cancel(run_id, actor))

    @app.post('/api/platform/runs/{run_id}/resume')
    def resume(run_id: str, payload: CommentInput, tasks: BackgroundTasks, actor=Depends(principal)):
        state = store.resume(run_id, actor, payload.comment)
        if settings.dispatch == 'inline':
            tasks.add_task(process_run, store, settings, run_id, model_factory, registry_factory)
        return public_run(state)

    @app.post('/api/platform/runs/{run_id}/review')
    def review(run_id: str, payload: ReviewInput, actor=Depends(principal)):
        return public_run(store.review(run_id, actor, payload.verdict, payload.comment))

    @app.post('/api/platform/runs/{run_id}/close')
    def close(run_id: str, payload: CommentInput, actor=Depends(principal)):
        state = store.get(run_id, actor)
        if state.get('review', {}).get('verdict') not in {'authorized', 'false_positive'}:
            raise Conflict('恶意事件必须通过处置回查关闭')
        return public_run(store.transition(run_id, actor, 'review', {'reviewed'}, 'completed', payload.comment))

    @app.get('/api/platform/runs/{run_id}/events')
    def run_events(run_id: str, after: int = 0, actor=Depends(principal)):
        return store.events(run_id, actor, after=max(0, after))

    @app.get('/api/platform/runs/{run_id}/stream')
    async def stream(run_id: str, request: Request, after: int = 0, actor=Depends(principal)):
        await asyncio.to_thread(store.get, run_id, actor)
        try:
            cursor = max(after, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            raise HTTPException(422, '无效事件游标')
        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                try:
                    current_actor = await asyncio.to_thread(sessions.resolve, request.cookies.get('secops_platform', ''))
                    items = await asyncio.to_thread(store.events, run_id, current_actor, cursor)
                except PermissionError:
                    yield 'event: auth_expired\ndata: {}\n\n'
                    return
                for item in items:
                    cursor = item['id']
                    yield f'id: {cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n'
                if not items:
                    yield ': heartbeat\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.get('/api/platform/runs/{run_id}/report')
    def export(run_id: str, actor=Depends(principal)):
        state = public_run(store.get(run_id, actor))
        return Response(json.dumps(state, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{run_id}.json"'})

    @app.get('/api/platform/runs/{run_id}/evidence/{evidence_id}')
    def export_evidence(run_id: str, evidence_id: str, actor=Depends(principal)):
        state = store.get(run_id, actor)
        evidence = next((e for e in state['evidence'] if e['id'] == evidence_id), None)
        if not evidence or not evidence.get('archive'):
            raise KeyError('证据归档不存在')
        archive = EvidenceArchive(bucket=settings.attachment_bucket, endpoint=settings.attachment_endpoint)
        try:
            document = archive.get(evidence['archive'])
        except (ValueError, FileNotFoundError):
            raise Conflict('归档不可用或完整性校验失败，请核查归档存储')
        return Response(json.dumps(document, ensure_ascii=False, indent=2), media_type='application/json',
            headers={'Content-Disposition': f'attachment; filename="{evidence_id}.json"'})

    @app.get('/api/platform/runs/{run_id}/actions')
    def list_actions(run_id: str, actor=Depends(principal)):
        return action_service.list_for(run_id, actor)

    @app.post('/api/platform/runs/{run_id}/actions', status_code=201)
    def request_action(run_id: str, payload: ActionInput, actor=Depends(principal)):
        return action_service.request(run_id, actor, **payload.model_dump())

    @app.post('/api/platform/actions/{action_id}/approve')
    def approve(action_id: str, payload: ApproveInput, actor=Depends(principal)):
        return action_service.approve(action_id, actor, **payload.model_dump())

    @app.post('/api/platform/actions/{action_id}/execute')
    def execute(action_id: str, actor=Depends(principal)):
        return action_service.execute(action_id, actor)

    @app.post('/api/platform/actions/{action_id}/withdraw')
    def withdraw(action_id: str, payload: CommentInput, actor=Depends(principal)):
        return action_service.withdraw(action_id, actor, payload.comment)

    @app.post('/api/platform/actions/{action_id}/reconcile')
    def reconcile(action_id: str, actor=Depends(principal)):
        return action_service.reconcile(action_id, actor)

    @app.post('/api/platform/actions/{action_id}/verify')
    def verify(action_id: str, payload: CommentInput, actor=Depends(principal)):
        return action_service.verify(action_id, actor, payload.comment)

    @app.get('/api/platform/audit')
    def audits(actor=Depends(principal)):
        if not set(actor.roles) & {'admin', 'auditor', 'manager'}:
            raise PermissionError('需要审计权限')
        condition = audit.c.org == actor.org
        if '*' not in actor.assets:
            condition &= audit.c.asset.in_(actor.assets)
        with db.engine.connect() as connection:
            return [dict(r) for r in connection.execute(select(audit).where(condition).order_by(audit.c.id.desc()).limit(200)).mappings()]

    @app.get('/api/platform/connectors')
    def connectors(actor=Depends(principal)):
        return {'mode': settings.connector_mode, 'version': settings.connector_version,
            'tools': ['asset_context', 'security_logs', 'auth_logs', 'business_audit', 'authorization', 'playbook'],
            'configured': settings.connector_mode == 'simulated' or bool(settings.connector_url and settings.connector_key),
            'connection_verified': False, 'note': '配置存在不代表真实接口已联调；查询结果见运行轨迹'}

    @app.get('/metrics')
    def metrics(actor=Depends(principal)):
        if not set(actor.roles) & {'admin', 'auditor'}:
            raise PermissionError('需要监控权限')
        return Response(persisted_metrics(db, actor), media_type=CONTENT_TYPE_LATEST)

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'platform.html')

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
