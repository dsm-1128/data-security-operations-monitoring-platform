import json
import re

import httpx

from app.tools import TOOL_NAMES


def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if re.search(r'(password|secret|token|authorization|cookie|phone|email|customer_name)', k, re.I) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', value)
        return re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[PHONE]', value)
    return value


def bounded_json(client, method, url, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 64000:
                raise ValueError('响应超过 64KB，请缩小查询范围或分页')
            chunks.append(chunk)
        value = json.loads(b''.join(chunks))
        if not isinstance(value, dict):
            raise ValueError('连接器必须返回对象')
        return value


class GatewayConnector:
    simulated = False

    def __init__(self, base_url, token, version='v1', transport=None):
        self.base_url, self.token, self.version, self.transport = base_url.rstrip('/'), token, version, transport

    def request(self, method, path, body=None, operation_id=None):
        headers = {'Authorization': 'Bearer ' + self.token, 'X-Connector-Version': self.version}
        if operation_id:
            headers['Idempotency-Key'] = operation_id
        with httpx.Client(transport=self.transport, timeout=15, follow_redirects=False, trust_env=False) as client:
            return bounded_json(client, method, self.base_url + path, headers=headers, **({'json': body} if body is not None else {}))

    def read(self, name, query, context):
        if name not in TOOL_NAMES:
            raise PermissionError('工具不在白名单')
        return redact(self.request('POST', '/queries/' + name, query))

    def execute(self, operation_id, action):
        return self.request('POST', '/actions', {'operation_id': operation_id, 'action': action}, operation_id)

    def query(self, operation_id):
        if not re.fullmatch(r'ACT-[0-9a-f]{32}', operation_id):
            raise ValueError('无效操作 ID')
        return self.request('GET', '/actions/' + operation_id)


class SimulatedConnector:
    version = 'simulated-v1'
    simulated = True

    def execute(self, operation_id, action):
        return {'status': 'applied', 'external_id': operation_id, 'simulated': True}

    def query(self, operation_id):
        return {'status': 'applied', 'external_id': operation_id, 'simulated': True}
