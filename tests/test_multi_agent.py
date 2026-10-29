import copy
import json

import httpx
import pytest

from app.agent.loop import new_state
from app.agent.model import ModelClient, SimulatedModel, SYSTEM
from app.agent.tools import ToolRegistry
from app.platform.config import Settings, config_fingerprint
from app.seed import make_incident


def test_subset_restricts_definitions_and_execution_and_keeps_scope_checks():
    registry = ToolRegistry.demo().subset({'asset_context', 'authorization'})
    assert {t['function']['name'] for t in registry.definitions()} == {'asset_context', 'authorization'}
    case = make_incident('web_attack')
    with pytest.raises(PermissionError):
        registry.execute({'id': 'bad', 'name': 'security_logs', 'arguments': {}}, case)
    with pytest.raises(PermissionError):
        registry.execute({'id': 'bad', 'name': 'authorization', 'arguments': {'asset': 'another'}}, case)
    with pytest.raises(PermissionError):
        registry.subset({'security_logs'})
    assert registry.execute({'id': 'ok', 'name': 'authorization', 'arguments': {}}, case)['complete']


def test_custom_system_prompt_keeps_original_client_unchanged():
    received = []
    def endpoint(request):
        received.append(json.loads(request.content)['messages'][0]['content'])
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [
            {'id': 'human', 'function': {'name': 'request_human', 'arguments': '{"reason":"test"}'}}
        ]}}], 'usage': {'total_tokens': 5}})
    model = ModelClient('https://model.example/v1', 'test', 'key', httpx.MockTransport(endpoint))
    scoped = model.with_system_prompt('专业调查目标')
    scoped.decide([], [], 64000)
    model.decide([], [], 64000)
    assert received == ['专业调查目标', SYSTEM]


def test_agent_arch_env_validation_and_fingerprint(monkeypatch):
    assert Settings().agent_arch == 'single'
    monkeypatch.setenv('SECOPS_AGENT_ARCH', 'multi')
    multi = Settings.from_env()
    multi.validate_startup()
    assert multi.agent_arch == 'multi'
    assert config_fingerprint(multi) != config_fingerprint(Settings())
    with pytest.raises(ValueError):
        Settings(agent_arch='unknown').validate_startup()


def multi_run(scenario='web_attack', model=None, registry=None, checkpoint=lambda _: None, cancelled=lambda: False, **budgets):
    from app.agent.supervisor import MultiAgentSupervisor
    return MultiAgentSupervisor(model or SimulatedModel(), registry or ToolRegistry.demo()).run(
        new_state(make_incident(scenario), **budgets), checkpoint=checkpoint, cancelled=cancelled)


@pytest.mark.parametrize('scenario,verdict,impact,defense', [
    ('web_attack', 'malicious', 'not_observed', 'passed'),
    ('authorized_scan', 'authorized', 'not_observed', 'passed'),
    ('blocked_attack', 'malicious', 'not_observed', 'blocked'),
    ('account_anomaly', 'malicious', 'confirmed', 'passed'),
    ('missing_logs', None, None, None),
])
def test_five_scenarios_dynamic_routes_and_policy(scenario, verdict, impact, defense):
    saved = []
    result = multi_run(scenario, checkpoint=saved.append)
    json.dumps(result, allow_nan=False)
    assert result['status'] == ('waiting_review' if verdict else 'needs_attention')
    starts = [t['agent'] for t in result['trace'] if t['kind'] == 'subagent_start']
    assert starts[0] == 'TriageAgent'
    if scenario == 'account_anomaly':
        assert 'IdentityInvestigationAgent' in starts
        assert 'WebInvestigationAgent' not in starts
        assert 'auth_logs' in {e['tool'] for e in result['evidence']}
    else:
        assert 'WebInvestigationAgent' in starts
        assert 'IdentityInvestigationAgent' not in starts
    if verdict:
        assert 'ResponseAgent' in starts
        assert [result['report'][k] for k in ('verdict', 'impact', 'defense')] == [verdict, impact, defense]
        assert {'supervisor_decision', 'subagent_start', 'subagent_tool', 'subagent_end'} <= {t['kind'] for t in result['trace']}
    else:
        assert result['report'] is None
    assert all(s['status'] == 'running' and s['report'] is None for s in saved[:-1])
    assert len({e['id'] for e in result['evidence']}) == len(result['evidence'])


@pytest.mark.parametrize('phase', ['observation', 'handoff', 'delegation'])
def test_crash_resume_does_not_repeat_completed_queries(phase):
    from app.agent.supervisor import MultiAgentSupervisor
    executed, saved = [], []
    registry = ToolRegistry.demo()
    for name, handler in list(registry.handlers.items()):
        def counted(query, case, name=name, handler=handler):
            executed.append((name, json.dumps(query, sort_keys=True)))
            return handler(query, case)
        registry.handlers[name] = counted
    class Crash(Exception):
        pass
    def checkpoint(state):
        saved.append(copy.deepcopy(state))
        kind = state['trace'][-1]['kind']
        if (phase == 'observation' and kind == 'subagent_tool'
                or phase == 'handoff' and kind == 'subagent_end'
                or phase == 'delegation' and kind == 'supervisor_decision'):
            raise Crash()
    loop = MultiAgentSupervisor(SimulatedModel(), registry)
    with pytest.raises(Crash):
        loop.run(new_state(make_incident('web_attack')), checkpoint=checkpoint)
    result = loop.run(saved[-1])
    assert result['status'] == 'waiting_review'
    assert len(executed) == len(set(executed)) == 5
    assert len(result['evidence']) == 5


class RoleModels:
    """Override one model boundary while keeping other roles' offline policies."""
    def __init__(self, role, decision=None, error=None):
        self.role, self.decision, self.error = role, decision, error

    def for_role(self, role, prompt):
        from app.agent.subagents import SimulatedSubAgentModel
        from app.agent.supervisor import SimulatedSupervisorModel
        if role != self.role:
            return SimulatedSupervisorModel() if role == 'Supervisor' else SimulatedSubAgentModel(role)
        return self

    def decide(self, *args):
        if self.error:
            raise self.error
        return copy.deepcopy(self.decision)


@pytest.mark.parametrize('name,args', [('auth_logs', {}), ('authorization', {'asset': 'other-company'})])
def test_specialist_rejects_forbidden_or_cross_asset_call(name, args):
    result = multi_run(model=RoleModels('TriageAgent', {'kind': 'tools', 'calls': [{'id': 'bad', 'name': name, 'arguments': args}]}))
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert result['evidence'] == []


def test_fabricated_subagent_citation_requires_human():
    result = multi_run(model=RoleModels('TriageAgent', {'kind': 'final', 'report': {'summary': 'done', 'evidence_ids': ['EV-invented']}}))
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert not result['multi_agent']['results'] or result['multi_agent']['results'][-1]['status'] == 'needs_attention'


def test_supervisor_cannot_finish_without_required_evidence():
    result = multi_run(model=RoleModels('Supervisor', {'kind': 'final', 'report': {'summary': 'done', 'evidence_ids': []}}))
    assert result['status'] == 'needs_attention'
    assert result['report'] is None


@pytest.mark.parametrize('role', ['Supervisor', 'TriageAgent'])
def test_model_errors_require_human_without_leaking_secrets(role):
    result = multi_run(model=RoleModels(role, error=RuntimeError('secret-value')))
    assert result['status'] == 'needs_attention'
    assert 'secret-value' not in json.dumps(result)


def test_connector_error_requires_human():
    registry = ToolRegistry.demo()
    def broken(*_):
        raise RuntimeError('secret-connector-error')
    registry.handlers['asset_context'] = broken
    result = multi_run(registry=registry)
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert 'secret-connector-error' not in json.dumps(result)


@pytest.mark.parametrize('budget', [{'max_tools': 1}, {'max_rounds': 2}, {'max_tokens': 0}, {'max_seconds': 0}])
def test_global_budget_is_not_reset_by_specialists(budget):
    result = multi_run(**budget)
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert result['rounds'] <= result['limits']['rounds']
    assert result['tool_count'] <= result['limits']['tools']


def test_cancel_before_planning():
    result = multi_run(cancelled=lambda: True)
    assert result['status'] == 'cancelled'
    assert result['rounds'] == 0


def test_tokens_charged_across_subagent_boundary():
    model = RoleModels('TriageAgent', {'kind': 'tools', 'calls': [{'id': 'a', 'name': 'asset_context', 'arguments': {}}], 'usage': 11})
    result = multi_run(model=model, max_tokens=10)
    assert result['tokens'] == 11
    assert result['status'] == 'needs_attention'
    assert result['evidence'] == []


@pytest.mark.parametrize('scenario', ['web_attack', 'authorized_scan', 'blocked_attack', 'account_anomaly', 'missing_logs'])
def test_existing_api_creates_multi_investigations(tmp_path, scenario):
    from fastapi.testclient import TestClient
    from app.platform.api import create_platform_app
    settings = Settings(agent_arch='multi', database_url='sqlite:///' + str(tmp_path / 'api.db'))
    with TestClient(create_platform_app(settings)) as client:
        assert client.post('/api/platform/session/demo', json={'role': 'analyst'}).status_code == 200
        response = client.post('/api/platform/runs', json={'scenario': scenario})
        assert response.status_code == 201
        run_id = response.json()['id']
        result = client.get('/api/platform/runs/' + run_id).json()
        assert any(t['kind'] == 'supervisor_decision' for t in result['trace'])
        assert result['status'] == ('needs_attention' if scenario == 'missing_logs' else 'waiting_review')
        assert 'fixture' not in result['case']
        evidence = result['evidence'][0]
        assert client.get(f'/api/platform/runs/{run_id}/evidence/{evidence["id"]}').status_code == 200
        events = client.get(f'/api/platform/runs/{run_id}/events').json()
        assert events and client.get(f'/api/platform/runs/{run_id}/events?after={events[-1]["id"]}').json() == []


@pytest.mark.parametrize('created_arch,worker_arch', [('single', 'multi'), ('multi', 'single')])
def test_runtime_refuses_snapshot_from_other_arch(tmp_path, created_arch, worker_arch):
    from app.platform.auth import demo_principal
    from app.platform.database import Database
    from app.platform.runs import RunStore
    from app.platform.runtime import process_run
    store = RunStore(Database('sqlite:///' + str(tmp_path / 'runs.db'), initialize=True))
    state = store.create(make_incident('web_attack'), demo_principal('analyst'), 'simulated',
                         config_fingerprint=config_fingerprint(Settings(agent_arch=created_arch)))
    result = process_run(store, Settings(agent_arch=worker_arch), state['id'])
    assert result['status'] == 'needs_attention'
    assert result['rounds'] == 0
    assert result['evidence'] == []


def assert_paired_calls(messages):
    pending = set()
    answered = set()
    for message in messages:
        if message['role'] == 'assistant':
            assert not pending
            pending = {c['id'] for c in message.get('tool_calls', [])}
            # Providers may reuse a tool ID in a later assistant turn; not in one turn.
            answered.clear()
        elif message['role'] == 'tool':
            assert message['tool_call_id'] in pending - answered
            answered.add(message['tool_call_id'])
            if pending == answered:
                pending.clear()
        else:
            assert not pending
    assert not pending


def test_human_resume_keeps_evidence_and_updates_current_agent_and_supervisor(tmp_path):
    from app.platform.auth import demo_principal
    from app.platform.database import Database
    from app.platform.runs import RunStore
    from app.platform.runtime import process_run
    settings = Settings(agent_arch='multi', database_url='sqlite:///' + str(tmp_path / 'resume.db'))
    store = RunStore(Database(settings.database_url, initialize=True))
    actor = demo_principal('analyst')
    state = store.create(make_incident('missing_logs'), actor, 'simulated', config_fingerprint=config_fingerprint(settings))
    registry = ToolRegistry.demo()
    first = process_run(store, settings, state['id'], registry_factory=lambda: registry)
    assert first['status'] == 'needs_attention'
    ids = {e['id'] for e in first['evidence']}
    original = registry.handlers['business_audit']
    def repaired(query, case):
        data = original(query, case)
        data['complete'] = True
        data['data']['coverage'] = 'complete'
        return data
    registry.handlers['business_audit'] = repaired
    store.resume(state['id'], actor, '日志权限已经补齐，请继续核查')
    result = process_run(store, settings, state['id'], registry_factory=lambda: registry)
    assert result['status'] == 'waiting_review'
    assert ids <= {e['id'] for e in result['evidence']}
    assert len(result['evidence']) == 6  # Five tools, plus the repaired incomplete audit.
    assert sum(e['tool'] == 'security_logs' for e in result['evidence']) == 1
    assert '日志权限已经补齐' in json.dumps(result['messages'], ensure_ascii=False)
    local = result['multi_agent']['subagent_states']['WebInvestigationAgent']
    assert '日志权限已经补齐' in json.dumps(local['messages'], ensure_ascii=False)
    assert_paired_calls(result['messages'])
    assert_paired_calls(local['messages'])


def test_web_export_evidence_dynamically_adds_identity_investigation():
    registry = ToolRegistry.demo()
    original = registry.handlers['business_audit']
    def export(query, case):
        result = original(query, case)
        result['data']['sensitive_export'] = True
        return result
    registry.handlers['business_audit'] = export
    result = multi_run(registry=registry, max_rounds=20)
    starts = [t['agent'] for t in result['trace'] if t['kind'] == 'subagent_start']
    assert starts == ['TriageAgent', 'WebInvestigationAgent', 'IdentityInvestigationAgent', 'ResponseAgent']
    assert result['report']['impact'] == 'confirmed'
    assert sum(e['tool'] == 'business_audit' for e in result['evidence']) == 1
    assert sum(e['tool'] == 'security_logs' for e in result['evidence']) == 1


def test_cached_forbidden_pending_call_is_revalidated_after_recovery():
    from app.agent.supervisor import MultiAgentSupervisor
    saved = []
    class Crash(Exception):
        pass
    def checkpoint(state):
        saved.append(state)
        if state['multi_agent']['active_agent'] == 'TriageAgent' and state['multi_agent']['subagent_states']['TriageAgent']['pending']:
            raise Crash()
    registry = ToolRegistry.demo()
    with pytest.raises(Crash):
        MultiAgentSupervisor(SimulatedModel(), registry).run(new_state(make_incident('web_attack')), checkpoint=checkpoint)
    state = saved[-1]
    import hashlib
    call = {'id': 'forbidden', 'name': 'auth_logs', 'arguments': {}}
    fingerprint = hashlib.sha256(json.dumps(['auth_logs', {}], sort_keys=True).encode()).hexdigest()
    cached = registry.execute(call, state['case'])
    state['query_cache'][fingerprint] = cached
    state['evidence'].append(cached)
    state['multi_agent']['subagent_states']['TriageAgent']['pending'] = [call]
    result = MultiAgentSupervisor(SimulatedModel(), registry).run(state)
    assert result['status'] == 'needs_attention'
    tool = next(t for t in result['trace'] if t['kind'] == 'subagent_tool')
    assert not tool['ok'] and tool['evidence_id'] is None


def test_mid_investigation_cancel_keeps_observation():
    flag = []
    def checkpoint(state):
        if state['evidence']:
            flag.append(True)
    result = multi_run(checkpoint=checkpoint, cancelled=lambda: bool(flag))
    assert result['status'] == 'cancelled'
    assert len(result['evidence']) == 1
    assert result['report'] is None


def test_public_multi_snapshot_does_not_expose_private_message_contexts(tmp_path):
    from app.platform.api import public_run
    result = multi_run()
    result.update(mode='simulated')
    public = public_run(result)
    assert 'messages' not in public
    assert 'subagent_states' not in public.get('multi_agent', {})
    assert public['report']['model_used'] is False
    assert 'Supervisor' in public['report']['engine']


def test_human_resume_after_planned_but_not_started_delegation(tmp_path):
    from app.agent.supervisor import MultiAgentSupervisor
    from app.platform.auth import demo_principal
    from app.platform.database import Database
    from app.platform.runs import RunStore
    from app.platform.runtime import process_run
    from app.platform.runs import Conflict
    settings = Settings(agent_arch='multi', database_url='sqlite:///' + str(tmp_path / 'staged.db'))
    store = RunStore(Database(settings.database_url, initialize=True))
    actor = demo_principal('analyst')
    state = store.create(make_incident('web_attack'), actor, 'simulated', config_fingerprint=config_fingerprint(settings))
    lease = store.claim(state['id'], 'worker')
    def checkpoint(snapshot):
        if snapshot['multi_agent']['phase'] == 'delegate':
            snapshot.update(status='needs_attention', reason='人工检查后继续')
            store.save(snapshot, lease)
            raise Conflict('模拟停止')
    with pytest.raises(Conflict):
        MultiAgentSupervisor(SimulatedModel(), ToolRegistry.demo()).run(store.get_internal(state['id']), checkpoint=checkpoint)
    store.resume(state['id'], actor, '核查完毕，继续原调查')
    result = process_run(store, settings, state['id'])
    assert result['status'] == 'waiting_review'
    assert_paired_calls(result['messages'])


def test_model_protocol_live_supervisor_and_specialist_scopes():
    from app.agent.subagents import SimulatedSubAgentModel
    from app.agent.supervisor import SimulatedSupervisorModel
    received = []
    def endpoint(request):
        payload = json.loads(request.content)
        prompt = payload['messages'][0]['content']
        names = {t['function']['name'] for t in payload['tools']} - {'submit_report', 'request_human'}
        messages = []
        for m in payload['messages'][1:]:
            m = copy.deepcopy(m)
            if m['role'] == 'tool':
                m['observation'] = json.loads(m.pop('content'))
            messages.append(m)
        if '你是安全运营 Supervisor' in prompt:
            assert names == {'delegate_agent'}
            role, policy = 'Supervisor', SimulatedSupervisorModel()
        else:
            expected = {
                'TriageAgent': {'asset_context', 'authorization'},
                'WebInvestigationAgent': {'security_logs', 'business_audit'},
                'ResponseAgent': {'playbook'},
            }
            role = next(r for r in expected if '你是 ' + r in prompt)
            assert names == expected[role]
            policy = SimulatedSubAgentModel(role)
        received.append(role)
        assert 'fixture' not in json.dumps(payload)
        assert_paired_calls(messages[:-1] if messages[-1]['role'] == 'user' else messages)
        decision = policy.decide(messages, [], 64000)
        if decision['kind'] == 'final':
            calls = [{'id': 'final', 'name': 'submit_report', 'arguments': decision['report']}]
        elif decision['kind'] == 'handoff':
            calls = [{'id': 'human', 'name': 'request_human', 'arguments': {'reason': decision['reason']}}]
        else:
            calls = decision['calls']
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [
            {'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': json.dumps(c['arguments'])}} for c in calls
        ]}}], 'usage': {'total_tokens': 10}})
    model = ModelClient('https://model.example/v1', 'test', 'key', httpx.MockTransport(endpoint))
    result = multi_run(model=model)
    assert result['status'] == 'waiting_review'
    assert set(received) == {'Supervisor', 'TriageAgent', 'WebInvestigationAgent', 'ResponseAgent'}
    assert result['tokens'] == len(received) * 10


def test_repeated_delegations_without_new_evidence_stop():
    model = RoleModels('Supervisor', {'kind': 'tools', 'calls': [{'id': 'again', 'name': 'delegate_agent', 'arguments': {
        'agent': 'TriageAgent', 'task': '再次核查授权', 'reason': '要求补查'}}]})
    result = multi_run(model=model)
    assert result['status'] == 'needs_attention'
    assert '未取得新证据' in result['reason']
    assert len(result['evidence']) == result['tool_count'] == 2
    assert len(result['multi_agent']['results']) >= 3


@pytest.mark.parametrize('call', [
    {'id': 'bad', 'name': 'security_logs', 'arguments': {}},
    {'id': 'bad', 'name': 'delegate_agent', 'arguments': {'agent': 'ShellAgent', 'task': '执行', 'reason': '越权'}},
])
def test_supervisor_cannot_read_enterprise_tools_or_invent_agents(call):
    result = multi_run(model=RoleModels('Supervisor', {'kind': 'tools', 'calls': [call]}))
    assert result['status'] == 'needs_attention'
    assert result['evidence'] == []
    assert result['tool_count'] == 0


@pytest.mark.parametrize('invented', [False, True])
def test_supervisor_reuses_original_final_evidence_gate(invented):
    from app.agent.supervisor import MultiAgentSupervisor
    state = multi_run()
    state.update(status='running', report=None, rounds=0, stalls=0)
    ids = [e['id'] for e in state['evidence']]
    if invented:
        ids.append('EV-fabricated')
    else:
        ids = [e['id'] for e in state['evidence'] if e['tool'] != 'business_audit']
    model = RoleModels('Supervisor', {'kind': 'final', 'report': {'summary': '不充分报告', 'evidence_ids': ids}})
    result = MultiAgentSupervisor(model, ToolRegistry.demo()).run(state)
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert any(t['kind'] == 'completion_rejected' for t in result['trace'])


def test_cross_agent_complete_query_cache_hit_is_not_new_evidence():
    from app.agent.subagents import SimulatedSubAgentModel
    class RequeryIdentity(RoleModels):
        def decide(self, messages, tools, remaining_tokens):
            if not any(m['role'] == 'tool' and m['observation'].get('tool') == 'security_logs' for m in messages):
                return {'kind': 'tools', 'calls': [{'id': 'requery', 'name': 'security_logs', 'arguments': {}}]}
            return SimulatedSubAgentModel('IdentityInvestigationAgent').decide(messages, tools, remaining_tokens)
    registry = ToolRegistry.demo()
    original = registry.handlers['business_audit']
    def export(query, case):
        result = original(query, case)
        result['data']['sensitive_export'] = True
        return result
    registry.handlers['business_audit'] = export
    result = multi_run(model=RequeryIdentity('IdentityInvestigationAgent'), registry=registry, max_rounds=24)
    assert result['status'] == 'waiting_review'
    assert sum(e['tool'] == 'security_logs' for e in result['evidence']) == 1
    hit = next(t for t in result['trace'] if t['kind'] == 'subagent_tool' and t['agent'] == 'IdentityInvestigationAgent' and t['tool'] == 'security_logs')
    assert hit['cache_hit'] is True
