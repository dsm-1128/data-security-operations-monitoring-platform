import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from .contracts import Query, ToolCall
from .evidence import validate_facts
from app.tools import TOOL_NAMES, call_tool
from app.seed import now


class ToolRegistry:
    def __init__(self, handlers, simulated=False, archive=None):
        self.handlers = handlers
        self.simulated = simulated
        self.archive = archive

    @classmethod
    def demo(cls):
        return cls({name: (lambda query, case, name=name: call_tool(name, case)) for name in TOOL_NAMES}, simulated=True)

    def definitions(self):
        return [{'type': 'function', 'function': {'name': name, 'description': TOOL_NAMES.get(name, name), 'parameters': Query.model_json_schema()}} for name in self.handlers]

    def subset(self, allowed_names):
        allowed = set(allowed_names)
        if not allowed <= self.handlers.keys():
            raise PermissionError('tool_scope_denied')
        return ToolRegistry({name: handler for name, handler in self.handlers.items() if name in allowed},
                            simulated=self.simulated, archive=self.archive)

    def validate_call(self, call, context):
        """Validate before cache lookup as well as before connector execution."""
        call = ToolCall.model_validate(call)
        if call.name not in self.handlers:
            raise PermissionError('unknown_tool')
        query = Query.model_validate(call.arguments)
        if query.asset and query.asset != context['asset']:
            raise PermissionError('asset_denied')
        if query.account and query.account != context.get('account'):
            raise PermissionError('account_denied')
        if query.source_ip and query.source_ip != context.get('ip'):
            raise PermissionError('source_denied')
        if query.path and query.path != context.get('path'):
            raise PermissionError('path_denied')
        if bool(query.start) != bool(query.end):
            raise ValueError('time_window_requires_start_and_end')
        if query.start:
            start, end = datetime.fromisoformat(query.start), datetime.fromisoformat(query.end)
            if not start.tzinfo or not end.tzinfo or not 0 < (end - start).total_seconds() <= 86400:
                raise ValueError('invalid_time_window')
        query.asset = context['asset']
        query.account = context.get('account')
        query.source_ip = context.get('ip')
        query.path = context.get('path')
        if not query.start:
            timestamp = datetime.fromisoformat(context.get('created_at', now()))
            query.start = (timestamp - timedelta(minutes=30)).isoformat()
            query.end = (timestamp + timedelta(minutes=30)).isoformat()
        return call, query

    def execute(self, call, context):
        call, query = self.validate_call(call, context)
        result = self.handlers[call.name](query.model_dump(), context)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 64000:
            raise ValueError('tool_result_too_large')
        if not isinstance(result.get('data'), dict) or not isinstance(result.get('complete'), bool):
            raise ValueError('invalid_tool_result')
        evidence = {'id': 'EV-' + uuid.uuid4().hex, 'tool': call.name, 'title': TOOL_NAMES.get(call.name, call.name),
                'source': result.get('source', call.name), 'at': now(), 'data': validate_facts(call.name, result['data']),
                'complete': result['complete'], 'summary': str(result.get('summary', ''))[:2000],
                'simulated': self.simulated, 'query_scope': query.model_dump(),
                'content_hash': hashlib.sha256(encoded).hexdigest(), 'ok': True}
        if self.archive:
            evidence['archive'] = self.archive(evidence)
        return evidence
