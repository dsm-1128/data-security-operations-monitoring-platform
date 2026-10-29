"""Exercise an existing development multi/simulated API; never production.

Container: PYTHONPATH=/app python /tmp/verify_multi_agent.py
Writes public snapshots and actual routing/tool traces to --output.
"""
import argparse
import json
import time
from pathlib import Path

import httpx

from app.platform.config import Settings


def verify(base_url, output):
    settings = Settings.from_env()
    if (settings.mode, settings.agent_arch, settings.model_mode, settings.connector_mode) != (
            'development', 'multi', 'simulated', 'simulated'):
        raise SystemExit('Requires development + multi + simulated model/connectors')
    scenarios = ['web_attack', 'authorized_scan', 'blocked_attack', 'account_anomaly', 'missing_logs']
    with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
        def post(path, body=None):
            response = client.post('/api/platform' + path, json=body or {})
            response.raise_for_status()
            return response.json()

        def get(path):
            response = client.get('/api/platform' + path)
            response.raise_for_status()
            return response.json()

        post('/session/demo', {'role': 'analyst'})
        created = [post('/runs', {'scenario': scenario}) for scenario in scenarios]
        started = time.monotonic()
        while True:
            results = [get('/runs/' + row['id']) for row in created]
            if all(r['status'] not in {'queued', 'running'} for r in results):
                break
            assert time.monotonic() - started < 60, 'Worker did not finish'
            time.sleep(0.25)
        for scenario, result in zip(scenarios, results):
            routes = [t['agent'] for t in result['trace'] if t['kind'] == 'subagent_start']
            assert routes[0] == 'TriageAgent'
            if scenario == 'missing_logs':
                assert result['status'] == 'needs_attention' and result['report'] is None
                continue
            assert result['status'] == 'waiting_review'
            assert result['report']['verdict'] == ('authorized' if scenario == 'authorized_scan' else 'malicious')
            if scenario == 'account_anomaly':
                assert result['report']['impact'] == 'confirmed'
                assert 'IdentityInvestigationAgent' in routes and 'WebInvestigationAgent' not in routes
            else:
                assert 'WebInvestigationAgent' in routes and 'IdentityInvestigationAgent' not in routes
            if scenario == 'blocked_attack':
                assert result['report']['defense'] == 'blocked'
            assert result['report']['model_used'] is False
            for item in result['trace']:
                if item['kind'] == 'subagent_tool':
                    allowed = {'TriageAgent': {'asset_context', 'authorization'},
                               'WebInvestigationAgent': {'security_logs', 'business_audit'},
                               'IdentityInvestigationAgent': {'auth_logs', 'business_audit', 'security_logs'},
                               'ResponseAgent': {'playbook'}}
                    assert item['tool'] in allowed[item['agent']]
        run = results[0]
        evidence = run['evidence'][0]
        assert get(f'/runs/{run["id"]}/evidence/{evidence["id"]}')['content_hash'] == evidence['content_hash']
        # Consume one real SSE event, then reconnect using Last-Event-ID.
        def first_event(after=None):
            headers = {'Last-Event-ID': str(after)} if after else {}
            with client.stream('GET', f'/api/platform/runs/{run["id"]}/stream', headers=headers) as response:
                response.raise_for_status()
                event_id = None
                for line in response.iter_lines():
                    if line.startswith('id: '):
                        event_id = int(line[4:])
                    elif line.startswith('data: '):
                        json.loads(line[6:])
                        return event_id
        first = first_event()
        assert first_event(first) > first
        # Existing approval / controlled simulated execution / verification chain.
        post(f'/runs/{run["id"]}/review', {'verdict': 'malicious', 'comment': '多 Agent 验证核对模拟证据'})
        action = post(f'/runs/{run["id"]}/actions', {'kind': 'block_ip', 'target': run['case']['ip'],
                      'duration_minutes': 30, 'reason': '多 Agent 集成验证模拟处置'})
        post('/session/demo', {'role': 'manager'})
        post(f'/actions/{action["id"]}/approve', {'approved': True, 'comment': '集成验证批准'})
        post('/session/demo', {'role': 'operator'})
        assert post(f'/actions/{action["id"]}/execute')['status'] == 'applied'
        post('/session/demo', {'role': 'analyst'})
        post(f'/actions/{action["id"]}/verify', {'comment': '集成验证回查模拟状态'})
        assert get('/runs/' + run['id'])['status'] == 'completed'
        document = {'model': 'simulated', 'connectors': 'simulated', 'agent_arch': 'multi',
                    'sse_reconnect': 'passed', 'human_execution_lifecycle': 'passed',
                    'scenarios': dict(zip(scenarios, results))}
        Path(output).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({name: {'status': r['status'], 'verdict': (r['report'] or {}).get('verdict'),
              'route': [t['agent'] for t in r['trace'] if t['kind'] == 'subagent_start'],
              'rounds': r['rounds'], 'tools': r['tool_count']} for name, r in zip(scenarios, results)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--output', default='/tmp/multi-agent-traces.json')
    args = parser.parse_args()
    verify(args.base_url, args.output)
