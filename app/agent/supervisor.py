"""Serial, recoverable Supervisor / professional-agent LangGraph."""
import copy
import json
import time

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from .contracts import Decision
from .evidence import effective_evidence
from .loop import AgentLoop, GraphState
from .subagents import AgentName, COMMON, SubAgent, SubAgentResult, public_case, role_model


SUPERVISOR_PROMPT = '''你是安全运营 Supervisor。独立理解事件并计划、委派、检查覆盖与重新规划。
只可通过 delegate_agent 串行委派专业 Agent，不能直接调用企业工具，不能审批或执行处置。
TriageAgent 核验资产和授权；WebInvestigationAgent 调查 Web 安全日志和业务审计；
IdentityInvestigationAgent 调查认证、安全日志、业务审计；ResponseAgent 读取手册提出人工审批建议。
根据 category、rule、已得证据、缺失要求、子任务结果和人工补充动态选择下一角色或要求补查；
不得固定跑所有 Agent。账号事件通常无需 Web Agent；Web 证据发现敏感导出时可增补 Identity Agent。
每次委派提供明确任务和简短可审计原因，不输出隐藏推理过程。
资产/授权初筛、适用专业调查和处置建议覆盖后，才可使用 submit_report 引用真实证据提交统一摘要。
最终至少引用完整有效 security_logs、authorization、business_audit；identity 还需 auth_logs。
风险 verdict/impact/defense 只能由确定性 policy_report 计算；你的解释不能覆写它。
子任务失败、证据不完整、无法推进或预算不足，request_human。''' + COMMON


class Delegation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent: AgentName
    task: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=500)


DELEGATE_TOOL = {'type': 'function', 'function': {
    'name': 'delegate_agent', 'description': '选择一个专业 Agent 调查或补查，提供任务和原因',
    'parameters': Delegation.model_json_schema()}}


class SimulatedSupervisorModel:
    """Deterministic offline planner using event fields and collected evidence."""
    def decide(self, messages, tools, remaining_tokens):
        context = json.loads(messages[-1]['content'])
        evidence = effective_evidence(context['evidence'])
        found = {e['tool']: e for e in evidence}
        completed = {r['agent'] for r in context['results'] if r['status'] == 'completed'}
        if any(not e['complete'] for e in evidence):
            return {'kind': 'handoff', 'reason': '现有证据查询不完整，需要人工补充'}
        if not {'asset_context', 'authorization'} <= found.keys() or 'TriageAgent' not in completed:
            agent, reason = 'TriageAgent', '核验目标资产、授权范围和时间窗口'
        else:
            identity = (context['case'].get('category') == 'identity'
                        or found.get('business_audit', {}).get('data', {}).get('sensitive_export', False))
            investigator = 'IdentityInvestigationAgent' if identity else 'WebInvestigationAgent'
            required = {'security_logs', 'business_audit'} | ({'auth_logs'} if identity else set())
            if not required <= found.keys() or investigator not in completed:
                agent, reason = investigator, ('账号事件或已发现敏感导出，关联认证与操作记录' if identity else 'Web 告警需核对请求、防护和业务审计，授权也须验证行为')
            elif 'playbook' not in found or 'ResponseAgent' not in completed:
                agent, reason = 'ResponseAgent', '专业事实已齐备，读取现有手册提出人工审批建议'
            else:
                return {'kind': 'final', 'report': {'summary': '离线模拟 Supervisor 已汇总专业调查与建议，等待人工复核。',
                                                  'evidence_ids': [e['id'] for e in evidence]}}
        return {'kind': 'tools', 'calls': [{'id': 'delegate-' + str(len(context['results'])), 'name': 'delegate_agent',
                                          'arguments': {'agent': agent, 'reason': reason, 'task': reason}}]}


class MultiAgentSupervisor(AgentLoop):
    def __init__(self, model, registry):
        self.base_model, self.registry = model, registry
        self.model = role_model(model, 'Supervisor', SUPERVISOR_PROMPT)

    def run(self, state, checkpoint=lambda state: None, cancelled=lambda: False):
        state = copy.deepcopy(state)
        state.setdefault('multi_agent', {'phase': 'supervisor', 'active_agent': None, 'plan': [],
                                        'subagent_states': {}, 'results': [], 'pending_delegation': None,
                                        'stalls': 0, 'synced_messages': len(state['messages'])})
        self._sync_human_input(state)

        def tick(graph_state):
            data = graph_state['data']
            self.step(data, cancelled)
            # Outside every model/connector catch: fencing/save failures MUST propagate.
            checkpoint(copy.deepcopy(data))
            return {'data': data}

        def route(s):
            data = s['data']
            return data['multi_agent']['phase'] if data['status'] == 'running' else END

        graph = StateGraph(GraphState)
        for name in ('supervisor', 'delegate', 'specialist'):
            graph.add_node(name, tick)
            graph.add_conditional_edges(name, route, {n: n for n in ('supervisor', 'delegate', 'specialist', END)})
        graph.add_conditional_edges(START, route, {n: n for n in ('supervisor', 'delegate', 'specialist', END)})
        return graph.compile().invoke({'data': state}, {'recursion_limit': 512})['data']

    @staticmethod
    def _sync_human_input(data):
        multi = data['multi_agent']
        new_messages = data['messages'][multi['synced_messages']:]
        human = [m for m in new_messages if m['role'] == 'user']
        if human:
            prefix = data['messages'][:multi['synced_messages']]
            unanswered = {}
            for message in prefix:
                if message['role'] == 'assistant':
                    unanswered.update({c['id']: c for c in message.get('tool_calls', [])})
                elif message['role'] == 'tool':
                    unanswered.pop(message['tool_call_id'], None)
            for call_id in unanswered:
                prefix.append({'role': 'tool', 'tool_call_id': call_id,
                               'observation': {'ok': False, 'reason': '上轮委派未结束，人工恢复'}})
            data['messages'] = prefix + new_messages
            local = multi['subagent_states'].get(multi['active_agent'])
            if local:
                # Match old single-mode resume: resolve unexecuted tool IDs, retain observations.
                for call in local['pending']:
                    local['messages'].append({'role': 'tool', 'tool_call_id': call['id'],
                                              'observation': {'ok': False, 'error': '上轮调用未执行，人工恢复'}})
                local.update(pending=[], stalls=0, result=None)
                local['messages'].extend(copy.deepcopy(human))
                local['messages'].append({'role': 'user', 'content': json.dumps({
                    'case': public_case(data['case']), 'evidence': data['evidence'], 'human_input': human}, ensure_ascii=False)})
                multi['phase'] = 'specialist'
            if multi['pending_delegation']:
                # A failed or interrupted delegation was closed above. Human
                # continuation gets a fresh, paired call ID, even before start.
                delegation = multi['pending_delegation']
                delegation['call_id'] = 'resume-' + str(len(data['messages']))
                data['messages'].append({'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': delegation['call_id'], 'type': 'function', 'function': {'name': 'delegate_agent',
                    'arguments': json.dumps({k: delegation[k] for k in ('agent', 'task', 'reason')})}}]})
            multi['stalls'] = 0
        multi['synced_messages'] = len(data['messages'])

    def step(self, data, cancelled=lambda: False):
        started, active_before = time.monotonic(), data['active_seconds']
        multi = data['multi_agent']
        if cancelled():
            data.update(status='cancelled', reason='用户取消运行')
        elif data['active_seconds'] >= data['limits']['seconds']:
            self.stop(data, '主动运行时间预算耗尽')
        elif multi['stalls'] >= 3 or data['stalls'] >= 3:
            self.stop(data, '主管连续调度未取得新证据，转人工')
        else:
            try:
                if multi['phase'] == 'supervisor':
                    self._plan(data)
                elif multi['phase'] == 'delegate':
                    self._start(data)
                elif multi['phase'] == 'specialist':
                    self._investigate(data, cancelled)
                else:
                    raise ValueError('invalid_multi_phase')
            except Exception:
                self.stop(data, '主管模型调用、委派或子 Agent 输出校验失败，请人工核查配置')
        # Child step already accounted for its own time; charge outer overhead exactly once.
        data['active_seconds'] += max(0, time.monotonic() - started - (data['active_seconds'] - active_before))
        if data['active_seconds'] > data['limits']['seconds'] and data['status'] in {'running', 'waiting_review'}:
            data['report'] = None
            self.stop(data, '主动运行时间预算耗尽')

    def _plan(self, data):
        if data['rounds'] >= data['limits']['rounds'] or data['tokens'] >= data['limits']['tokens']:
            self.stop(data, '模型轮次或 Token 预算耗尽')
            return
        multi = data['multi_agent']
        data['messages'].append({'role': 'user', 'content': json.dumps({
            'case': public_case(data['case']), 'evidence': data['evidence'], 'results': multi['results'],
            'remaining': {k: data['limits'][k] - data[v] for k, v in [('rounds', 'rounds'), ('tools', 'tool_count'), ('tokens', 'tokens')]}
        }, ensure_ascii=False)})
        data['rounds'] += 1
        decision = Decision.model_validate(self.model.decide(data['messages'], [DELEGATE_TOOL], data['limits']['tokens'] - data['tokens']))
        data['tokens'] += decision.usage
        if data['tokens'] > data['limits']['tokens']:
            self.stop(data, '模型 Token 预算耗尽')
        elif decision.kind == 'handoff':
            self.stop(data, decision.reason or '主管请求人工接管')
        elif decision.kind == 'final':
            completed = {r['agent'] for r in multi['results'] if r['status'] == 'completed'}
            appropriate = 'IdentityInvestigationAgent' if data['case'].get('category') == 'identity' else 'WebInvestigationAgent'
            if not {'TriageAgent', appropriate, 'ResponseAgent'} <= completed:
                self.stop(data, '专业子任务覆盖不足，不能提交正式报告')
            else:
                self._finish(data, decision.report, self.record)
                if data['status'] == 'waiting_review':
                    data['report']['engine'] = 'LangGraph Supervisor + SubAgents'
        else:
            if len(decision.calls) != 1 or decision.calls[0].name != 'delegate_agent':
                raise ValueError('supervisor_may_only_delegate')
            call = decision.calls[0]
            delegation = Delegation.model_validate(call.arguments)
            multi.update(phase='delegate', pending_delegation={**delegation.model_dump(), 'call_id': call.id})
            multi['plan'].append(delegation.model_dump())
            data['messages'].append({'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': call.id, 'type': 'function', 'function': {'name': call.name, 'arguments': json.dumps(call.arguments)}}]})
            self.record(data, 'supervisor_decision', agent=delegation.agent, reason=delegation.reason, task=delegation.task)
        multi['synced_messages'] = len(data['messages'])

    def _start(self, data):
        multi = data['multi_agent']
        delegation = multi['pending_delegation']
        validated = Delegation.model_validate({k: delegation[k] for k in ('agent', 'task', 'reason')})
        role = validated.agent
        local = multi['subagent_states'].setdefault(role, {'messages': [], 'pending': [], 'stalls': 0, 'result': None})
        if local['pending']:
            raise ValueError('unresolved_subagent_calls')
        local.update(result=None, evidence_before=len(data['evidence']))
        local['messages'].append({'role': 'user', 'content': json.dumps({
            'case': public_case(data['case']), 'task': validated.task, 'evidence': data['evidence']}, ensure_ascii=False)})
        multi.update(phase='specialist', active_agent=role)
        self.record(data, 'subagent_start', agent=role, reason=validated.reason)

    def _investigate(self, data, cancelled):
        multi = data['multi_agent']
        role = multi['active_agent']
        local = multi['subagent_states'][role]
        SubAgent(role, self.base_model, self.registry).step(data, cancelled)
        result = local.get('result')
        if data['status'] == 'needs_attention':
            result = SubAgentResult(agent=role, status='needs_attention', summary=data['reason'],
                                    evidence_ids=[], need_more=['人工核查日志、权限或模型配置'], reason=data['reason'][:500]).model_dump()
            local['result'] = result
        if result and data['status'] != 'cancelled':
            result = SubAgentResult.model_validate(result).model_dump()
            if not set(result['evidence_ids']) <= {e['id'] for e in data['evidence']}:
                raise ValueError('invalid_handoff_citations')
            multi['results'].append(result)
            data['messages'].append({'role': 'tool', 'tool_call_id': multi['pending_delegation']['call_id'], 'observation': result})
            multi['synced_messages'] = len(data['messages'])
            self.record(data, 'subagent_end', agent=role, reason=result['reason'] or result['status'], summary=result['summary'])
            if data['status'] == 'running':
                multi['stalls'] = multi['stalls'] + 1 if len(data['evidence']) == local['evidence_before'] else 0
                multi.update(phase='supervisor', active_agent=None, pending_delegation=None)
