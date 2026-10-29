import copy
import hashlib
import json
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .contracts import Decision
from app.seed import now
from .evidence import effective_evidence, policy_report


def new_state(case, max_rounds=12, max_tools=24, max_seconds=300, max_tokens=64000):
    public = {k: case.get(k) for k in ('id', 'name', 'category', 'asset', 'account', 'ip', 'path')}
    return {'case': copy.deepcopy(case), 'status': 'running', 'rounds': 0, 'tool_count': 0, 'tokens': 0,
            'active_seconds': 0.0, 'limits': {'rounds': max_rounds, 'tools': max_tools, 'seconds': max_seconds, 'tokens': max_tokens},
            'messages': [{'role': 'user', 'content': json.dumps(public, ensure_ascii=False)}],
            'pending': [], 'evidence': [], 'trace': [], 'report': None, 'reason': '', 'query_cache': {}, 'stalls': 0}


class GraphState(TypedDict):
    data: dict


class AgentLoop:
    def __init__(self, model, registry):
        self.model, self.registry = model, registry

    def run(self, state, checkpoint=lambda state: None, cancelled=lambda: False):
        state = copy.deepcopy(state)

        def tick(graph_state):
            data = graph_state['data']
            self.step(data, cancelled)
            # Checkpoint errors MUST propagate: a stale worker may never keep running.
            checkpoint(copy.deepcopy(data))
            return {'data': data}

        graph = StateGraph(GraphState)
        graph.add_node('agent_step', tick)
        graph.add_edge(START, 'agent_step')
        graph.add_conditional_edges('agent_step', lambda s: 'continue' if s['data']['status'] == 'running' else 'end', {'continue': 'agent_step', 'end': END})
        return graph.compile().invoke({'data': state}, {'recursion_limit': 256})['data']

    @staticmethod
    def record(data, kind, **values):
        data['trace'].append({'kind': kind, 'at': now(), **values})

    @staticmethod
    def stop(data, reason):
        data.update(status='needs_attention', reason=reason)
        AgentLoop.record(data, 'handoff', reason=reason)

    def step(self, data, cancelled=lambda: False):
        record, stop = self.record, self.stop
        started = time.monotonic()
        if cancelled():
            data.update(status='cancelled', reason='用户取消运行')
        elif data['active_seconds'] >= data['limits']['seconds']:
            stop(data, '主动运行时间预算耗尽')
        elif data['stalls'] >= 3:
            stop(data, '连续调用没有取得新证据')
        elif data['pending']:
            call = data['pending'][0]
            if data['tool_count'] >= data['limits']['tools']:
                stop(data, '工具调用预算耗尽')
            else:
                data['tool_count'] += 1
                cache_hit = False
                fingerprint = hashlib.sha256(json.dumps([call['name'], call['arguments']], sort_keys=True).encode()).hexdigest()
                try:
                    self.registry.validate_call(call, data['case'])
                    if fingerprint in data['query_cache']:
                        cache_hit = True
                        observation = data['query_cache'][fingerprint]
                        data['stalls'] += 1
                    else:
                        observation = self.registry.execute(call, data['case'])
                        data['evidence'].append(observation)
                        if observation['complete']:
                            data['query_cache'][fingerprint] = observation
                        data['stalls'] = 0
                except (PermissionError, ValueError, ValidationError):
                    observation = {'ok': False, 'tool': call['name'], 'error': '工具名、参数、权限或结果校验失败，请修正或转人工'}
                    data['stalls'] += 1
                except Exception:
                    observation = {'ok': False, 'tool': call['name'], 'error': '连接器不可用，可在预算内调整查询或请求人工'}
                    data['stalls'] += 1
                data['messages'].append({'role': 'tool', 'tool_call_id': call['id'], 'observation': observation})
                record(data, 'tool', tool=call['name'], call_id=call['id'], ok=observation['ok'], cache_hit=cache_hit, evidence_id=observation.get('id'), summary=observation.get('summary', observation.get('error', '')))
                data['pending'].pop(0)
        elif data['rounds'] >= data['limits']['rounds'] or data['tokens'] >= data['limits']['tokens']:
            stop(data, '模型轮次或 Token 预算耗尽')
        else:
            data['rounds'] += 1
            try:
                decision = Decision.model_validate(self.model.decide(data['messages'], self.registry.definitions(), data['limits']['tokens'] - data['tokens']))
                data['tokens'] += decision.usage
                if data['tokens'] > data['limits']['tokens']:
                    stop(data, '模型 Token 预算耗尽')
                elif decision.kind == 'tools':
                    if not decision.calls or len({c.id for c in decision.calls}) != len(decision.calls):
                        raise ValueError('invalid_calls')
                    data['pending'] = [c.model_dump() for c in decision.calls]
                    data['messages'].append({'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': c.id, 'type': 'function', 'function': {'name': c.name, 'arguments': json.dumps(c.arguments)}} for c in decision.calls]})
                    record(data, 'decision', tools=[c.name for c in decision.calls])
                elif decision.kind == 'handoff':
                    stop(data, decision.reason or '模型请求人工接管')
                else:
                    self._finish(data, decision.report, record)
            except Exception:
                stop(data, '模型调用或输出校验失败，请检查模型配置后重新调查')
        data['active_seconds'] += time.monotonic() - started
        if data['active_seconds'] > data['limits']['seconds'] and data['status'] in {'running', 'waiting_review'}:
            data['report'] = None
            stop(data, '主动运行时间预算耗尽')

    @staticmethod
    def _finish(data, report, record):
        required = {'security_logs', 'authorization', 'business_audit'}
        if data['case'].get('category') == 'identity':
            required.add('auth_logs')
        evidence = data['evidence']
        cited = set(report.evidence_ids) if report else set()
        selected = [e for e in evidence if e['id'] in cited]
        effective = effective_evidence(evidence)
        valid = report and cited and cited <= {e['id'] for e in evidence} and required <= {e['tool'] for e in selected} and all(e['complete'] for e in effective)
        if not valid:
            data['stalls'] += 1
            data['messages'].append({'role': 'user', 'content': '完成校验失败：请引用现有证据，补齐安全日志、业务审计、授权和适用的认证日志；查询不完整时请求人工。'})
            record(data, 'completion_rejected', reason='必要证据缺失、引用无效或查询不完整')
            return
        policy = policy_report(evidence)
        policy.update(model_narrative=report.summary, engine='LangGraph Agent Loop', model_used=True)
        data.update(status='waiting_review', report=policy, reason='调查完成，等待人工复核')
        record(data, 'report', verdict=policy['verdict'])
