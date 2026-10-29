import json
import httpx
import pytest

from app.agent.model import ModelClient


def test_real_adapter_sends_tools_and_observations_and_parses_call():
    received = []
    def endpoint(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [{'id': 'call-2', 'function': {'name': 'security_logs', 'arguments': '{"limit":10}'}}]}}], 'usage': {'total_tokens': 75}})
    model = ModelClient('https://llm.example/v1', 'test', 'secret', httpx.MockTransport(endpoint))
    result = model.decide([{'role': 'user', 'content': '调查订单告警'}, {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'asset_context', 'arguments': '{}'}}]}, {'role': 'tool', 'tool_call_id': 'call-1', 'observation': {'asset': 'orders'}}], [], 64000)
    assert received[0]['messages'][-1]['tool_call_id'] == 'call-1'
    assert json.loads(received[0]['messages'][-1]['content']) == {'asset': 'orders'}
    assert received[0]['tool_choice'] == 'required'
    assert result['calls'][0]['arguments'] == {'limit': 10}
    assert result['usage'] == 75


def test_budget_prevents_network_request():
    def should_not_call(request):
        raise AssertionError('Network must not be reached')
    model = ModelClient('https://llm.example/v1', 'test', 'secret', httpx.MockTransport(should_not_call))
    with pytest.raises(ValueError, match='budget'):
        model.decide([{'role': 'user', 'content': '调查'}], [], 10)
