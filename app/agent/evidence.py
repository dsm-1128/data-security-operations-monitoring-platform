import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool
from app.seed import now


class Facts(BaseModel):
    model_config = ConfigDict(extra='allow')


class SecurityFacts(Facts):
    suspicious_pattern: StrictBool
    final_action: Literal['block', 'observe', 'allow', 'pass']


class AuditFacts(Facts):
    sensitive_export: StrictBool


class AuthorizationFacts(Facts):
    authorized: StrictBool
    scope_matches: StrictBool
    within_window: StrictBool


class IdentityFacts(Facts):
    unexpected_login: StrictBool
    mfa_passed: StrictBool
    owner_confirmed_unexpected: StrictBool


class AssetFacts(Facts):
    asset: str
    owner: str


class PlaybookFacts(Facts):
    document_id: str
    version: str
    steps: list[str]


FACT_SCHEMAS = {'security_logs': SecurityFacts, 'business_audit': AuditFacts, 'authorization': AuthorizationFacts,
                'auth_logs': IdentityFacts, 'asset_context': AssetFacts, 'playbook': PlaybookFacts}


def validate_facts(tool, data):
    return FACT_SCHEMAS[tool].model_validate(data).model_dump()


def effective_evidence(evidence):
    latest = {}
    for item in evidence:
        key = (item['tool'], json.dumps(item.get('query_scope', {}), sort_keys=True))
        latest[key] = item
    return list(latest.values())


def policy_report(evidence):
    # Positive facts are never erased by another page or a later empty query.
    security = [e['data'] for e in evidence if e['tool'] == 'security_logs']
    audits = [e['data'] for e in evidence if e['tool'] == 'business_audit']
    authorizations = [e['data'] for e in effective_evidence(evidence) if e['tool'] == 'authorization']
    exported = any(e['sensitive_export'] for e in audits)
    account_misuse = any(e['data']['unexpected_login'] and e['data']['owner_confirmed_unexpected']
                        for e in evidence if e['tool'] == 'auth_logs')
    suspicious = any(e['suspicious_pattern'] for e in security) or account_misuse
    authorized = bool(authorizations) and all(e['authorized'] and e['scope_matches'] and e['within_window'] for e in authorizations)
    if authorized and (exported or account_misuse):
        verdict, impact = 'inconclusive', 'confirmed' if exported else 'unknown'
        summary = '存在敏感导出或负责人确认的非本人登录，同时存在授权记录；须人工核对授权范围与冲突，不能直接按授权活动关闭。'
    elif authorized:
        verdict, impact = 'authorized', 'not_observed'
        summary = '已获得匹配范围与时间的授权证据，请人工核验后归档。'
    elif exported or suspicious:
        verdict, impact = 'malicious', 'confirmed' if exported else 'not_observed'
        summary = '证据中存在敏感导出事实，需要核查访问范围并提交受控处置。' if exported else '识别到恶意行为，当前已查证据未确认业务损害；防护拦截不等于误报。'
    else:
        verdict, impact = 'inconclusive', 'unknown'
        summary = '当前证据不足以确认行为性质，需要人工核验。'
    return {'verdict': verdict, 'impact': impact, 'defense': 'blocked' if security and all(e['final_action'] == 'block' for e in security) else 'passed',
        'summary': summary, 'missing': [], 'evidence_ids': [e['id'] for e in evidence],
        'recommendations': ['核验所有证据与授权范围', '高风险动作提交人工审批', '核对执行结果与业务影响'],
        'generated_at': now(), 'engine': 'LangGraph Agent Loop', 'model_used': True}
