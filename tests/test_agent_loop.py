import copy

import pytest

from app.agent.loop import AgentLoop, new_state
from app.agent.tools import ToolRegistry
from app.seed import make_incident


class AdaptiveModel:
    """A protocol double: chooses next action from observations, not call count."""
    def decide(self, messages, tools, remaining_tokens):
        observations = [m['observation'] for m in messages if m['role'] == 'tool']
        found = {o['tool']: o for o in observations if o.get('ok')}
        names = ['security_logs', 'authorization', 'business_audit']
        if found.get('business_audit', {}).get('data', {}).get('sensitive_export'):
            names.append('auth_logs')
        for name in names:
            if name not in found:
                return {'kind': 'tools', 'calls': [{'id': 'call-' + name, 'name': name, 'arguments': {}}], 'usage': 30}
        return {'kind': 'final', 'report': {'summary': '证据表明需要人工复核', 'evidence_ids': [o['id'] for o in found.values()]}, 'usage': 40}


def run(scenario='web_attack', model=None, **budget):
    case = make_incident(scenario)
    state = new_state(case, **budget)
    return AgentLoop(model or AdaptiveModel(), ToolRegistry.demo()).run(state)


def test_observation_changes_next_tool():
    web = run()
    identity = run('account_anomaly')
    assert web['status'] == identity['status'] == 'waiting_review'
    assert 'auth_logs' not in [e['tool'] for e in web['evidence']]
    assert 'auth_logs' in [e['tool'] for e in identity['evidence']]
    assert web['rounds'] >= 4


class ConstantModel:
    def __init__(self, decision):
        self.decision = decision

    def decide(self, messages, tools, remaining_tokens):
        return copy.deepcopy(self.decision)


def test_premature_final_does_not_complete():
    result = run(model=ConstantModel({'kind': 'final', 'report': {'summary': '没有风险', 'evidence_ids': []}}), max_rounds=3)
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert any(t['kind'] == 'completion_rejected' for t in result['trace'])


def test_unknown_and_cross_asset_tools_never_execute():
    for name, args in [('shell', {}), ('security_logs', {'asset': 'other-company'})]:
        result = run(model=ConstantModel({'kind': 'tools', 'calls': [{'id': 'x', 'name': name, 'arguments': args}]}), max_rounds=2)
        assert result['evidence'] == []
        assert result['status'] == 'needs_attention'


def test_missing_logs_require_human():
    result = run('missing_logs')
    assert result['status'] == 'needs_attention'
    assert result['report'] is None


def test_identical_queries_stop_without_new_evidence():
    result = run(model=ConstantModel({'kind': 'tools', 'calls': [{'id': 'x', 'name': 'security_logs', 'arguments': {}}]}))
    assert result['status'] == 'needs_attention'
    assert result['rounds'] < 12
    assert len(result['evidence']) == 1


def test_model_failure_is_not_rule_fallback():
    class BrokenModel:
        def decide(self, *args):
            raise RuntimeError('secret-provider-error')
    result = run(model=BrokenModel())
    assert result['status'] == 'needs_attention'
    assert result['report'] is None
    assert 'secret-provider-error' not in str(result)


def test_budget_stops_before_next_tool():
    result = run(max_tools=1)
    assert result['status'] == 'needs_attention'
    assert len(result['evidence']) == 1


def test_checkpoint_after_observation_can_resume_without_repeating_query():
    saved = []
    def checkpoint(state):
        saved.append(copy.deepcopy(state))
    case = make_incident('web_attack')
    loop = AgentLoop(AdaptiveModel(), ToolRegistry.demo())
    loop.run(new_state(case), checkpoint=checkpoint)
    partial = next(s for s in saved if len(s['evidence']) == 1 and not s['pending'])
    result = loop.run(partial)
    assert result['status'] == 'waiting_review'
    assert len([e for e in result['evidence'] if e['tool'] == 'security_logs']) == 1


def test_cancel_callback_stops_loop():
    state = new_state(make_incident('web_attack'))
    result = AgentLoop(AdaptiveModel(), ToolRegistry.demo()).run(state, cancelled=lambda: True)
    assert result['status'] == 'cancelled'
    assert result['rounds'] == 0


def test_last_model_call_cannot_finish_after_time_budget():
    import time
    state = run()
    state.update(status='running', rounds=0, active_seconds=0, report=None)
    state['limits']['seconds'] = 0.01
    class SlowFinal:
        def decide(self, *args):
            time.sleep(0.03)
            return {'kind': 'final', 'report': {'summary': '报告', 'evidence_ids': [e['id'] for e in state['evidence']]}}
    result = AgentLoop(SlowFinal(), ToolRegistry.demo()).run(state)
    assert result['status'] == 'needs_attention'


def test_tool_query_contains_event_target_and_bounded_default_time():
    received = []
    def query(parameters, context):
        received.append(parameters)
        return {'data': {'suspicious_pattern': False, 'final_action': 'observe'}, 'complete': True}
    case = make_incident('web_attack')
    registry = ToolRegistry({'security_logs': query})
    registry.execute({'id': 'a', 'name': 'security_logs', 'arguments': {}}, case)
    assert received[0]['source_ip'] == '198.51.100.23'
    assert received[0]['path'] == '/api/orders/search'
    assert received[0]['start'] and received[0]['end']


def test_query_cannot_switch_source_ip():
    registry = ToolRegistry.demo()
    with pytest.raises((ValueError, PermissionError)):
        registry.execute({'id': 'a', 'name': 'security_logs', 'arguments': {'source_ip': '8.8.8.8'}}, make_incident('web_attack'))


def test_string_false_is_not_valid_authorization():
    registry = ToolRegistry({'authorization': lambda *_: {'complete': True, 'data': {'authorized': 'false', 'scope_matches': 'false', 'within_window': 'false'}}})
    with pytest.raises(ValueError):
        registry.execute({'id': 'a', 'name': 'authorization', 'arguments': {}}, make_incident('web_attack'))


def test_confirmed_account_misuse_does_not_require_web_attack_or_export():
    from app.agent.model import SimulatedModel
    case = make_incident('account_anomaly')
    case['fixture'].update(attack=False, export=False)
    result = AgentLoop(SimulatedModel(), ToolRegistry.demo()).run(new_state(case))
    assert result['report']['verdict'] == 'malicious'
    assert result['report']['impact'] == 'not_observed'


@pytest.mark.parametrize('omit_attack', [False, True])
def test_multiple_windows_cannot_erase_export_evidence(omit_attack):
    state = run()
    state.update(status='running', report=None, rounds=0)
    audit = next(e for e in state['evidence'] if e['tool'] == 'business_audit')
    attack = copy.deepcopy(audit)
    attack.update(id='EV-earlier-export', query_scope={**audit['query_scope'], 'cursor': 'earlier-page'})
    attack['data']['sensitive_export'] = True
    state['evidence'].insert(0, attack)
    ids = [e['id'] for e in state['evidence'] if not omit_attack or e['id'] != attack['id']]
    result = AgentLoop(ConstantModel({'kind': 'final', 'report': {'summary': '报告', 'evidence_ids': ids}}), ToolRegistry.demo()).run(state)
    assert result['status'] == 'waiting_review'
    assert result['report']['impact'] == 'confirmed'
    assert attack['id'] in result['report']['evidence_ids']
