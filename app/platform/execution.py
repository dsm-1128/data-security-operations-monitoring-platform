import copy
import hashlib
import json
import time
import uuid

from sqlalchemy import insert, select, update

from .auth import authorize
from .database import actions, runs
from .runs import Conflict, audit_record


def parameter_hash(parameters):
    return hashlib.sha256(json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ActionService:
    def __init__(self, store, connector, resolver=None):
        self.store, self.db, self.connector, self.resolver = store, store.db, connector, resolver

    def request(self, run_id, principal, kind, target, duration_minutes, reason):
        state = self.store.get(run_id, principal)
        authorize(principal, state['case']['asset'], 'request', state['org'])
        if state['status'] != 'reviewed' or state.get('review', {}).get('verdict') != 'malicious':
            raise Conflict('必须先人工复核为恶意活动')
        expected = state['case'].get('account') if kind == 'freeze_account' else state['case'].get('ip')
        if kind not in {'freeze_account', 'block_ip'} or not target or target != expected or not 1 <= duration_minutes <= 60:
            raise ValueError('处置目标或范围无效')
        if not reason.strip():
            raise ValueError('必须说明处置理由')
        parameters = {'kind': kind, 'target': target, 'asset': state['case']['asset'], 'duration_minutes': duration_minutes}
        document = {'id': 'ACT-' + uuid.uuid4().hex, 'run_id': run_id, 'org': state['org'], 'asset': state['case']['asset'],
            'requester': principal.subject, 'parameters': parameters, 'hash': parameter_hash(parameters), 'status': 'pending',
            'reason': reason, 'connector_version': self.connector.version, 'simulated': self.connector.simulated,
            'connector_identity': self._connector_identity(),
            'approval': None, 'result': None, 'created': time.time()}
        with self.db.engine.begin() as connection:
            connection.execute(select(runs.c.id).where(runs.c.id == run_id).with_for_update()).one()
            existing = connection.execute(select(actions.c.id).where(actions.c.run_id == run_id,
                actions.c.status.in_(['pending', 'approved', 'executing', 'execution_unknown', 'applied']))).first()
            if existing:
                raise Conflict('当前事件已有待处理处置，请先完成、驳回或撤回未执行申请')
            connection.execute(insert(actions).values(id=document['id'], run_id=run_id, org=state['org'], asset=document['asset'],
                status='pending', document=document, version=0, created=document['created']))
            audit_record(connection, principal, document['asset'], 'action_requested', document['id'], {'hash': document['hash']})
        return document

    def get(self, action_id, principal):
        with self.db.engine.connect() as connection:
            row = connection.execute(select(actions).where(actions.c.id == action_id)).mappings().first()
        if not row:
            raise KeyError('处置不存在')
        authorize(principal, row['asset'], 'read', row['org'])
        return copy.deepcopy(row['document'])

    def list_for(self, run_id, principal):
        self.store.get(run_id, principal)
        with self.db.engine.connect() as connection:
            return list(connection.execute(select(actions.c.document).where(actions.c.run_id == run_id).order_by(actions.c.created.desc())).scalars())

    def _change(self, action_id, principal, permission, allowed, mutate, after=None):
        with self.db.engine.begin() as connection:
            row = connection.execute(select(actions).where(actions.c.id == action_id).with_for_update()).mappings().first()
            if not row:
                raise KeyError('处置不存在')
            authorize(principal, row['asset'], permission, row['org'])
            if row['status'] not in allowed:
                raise Conflict('处置状态已变化或不允许重复执行')
            document = copy.deepcopy(row['document'])
            mutate(document)
            result = connection.execute(update(actions).where(actions.c.id == action_id, actions.c.version == row['version']).values(
                document=document, status=document['status'], version=row['version'] + 1))
            if result.rowcount != 1:
                raise Conflict('处置记录已更新')
            if after:
                after(connection, document)
            audit_record(connection, principal, row['asset'], permission, action_id, {'status': document['status']})
        return document

    def approve(self, action_id, principal, approved, comment, ttl=1800):
        def mutate(document):
            if principal.subject == document['requester']:
                raise PermissionError('不能审批自己提交的处置')
            self._check_connector(document)
            document.update(status='approved' if approved else 'rejected', approval={'subject': principal.subject,
                'hash': document['hash'], 'expires': time.time() + ttl, 'comment': comment})
        return self._change(action_id, principal, 'approve', {'pending'}, mutate)

    def withdraw(self, action_id, principal, comment):
        def mutate(document):
            if principal.subject != document['requester']:
                authorize(principal, document['asset'], 'approve', document['org'])
            if len(comment.strip()) < 4:
                raise ValueError('请说明撤回原因')
            document.update(status='withdrawn', withdrawal={'subject': principal.subject,
                'comment': comment, 'at': time.time()})
        return self._change(action_id, principal, 'request', {'pending', 'approved'}, mutate)

    def execute(self, action_id, principal):
        old = self.get(action_id, principal)
        authorize(principal, old['asset'], 'execute', old['org'])
        if old['status'] in {'applied', 'verified'}:
            return old
        def claim(document):
            approval = document['approval']
            if not approval or approval['expires'] <= time.time() or approval['hash'] != parameter_hash(document['parameters']) or document['hash'] != approval['hash']:
                raise Conflict('审批过期或参数已变化')
            self._check_connector(document)
            if self.resolver:
                authorize(self.resolver(approval['subject']), document['asset'], 'approve', document['org'])
                authorize(self.resolver(document['requester']), document['asset'], 'request', document['org'])
            document.update(status='executing', execution_started=time.time(), executor=principal.subject)
        def check_run(connection, document):
            run = connection.execute(select(runs).where(runs.c.id == document['run_id']).with_for_update()).mappings().one()
            if run['status'] != 'reviewed' or run['state'].get('review', {}).get('verdict') != 'malicious':
                raise Conflict('调查状态已变化，不能执行旧审批')
        claimed = self._change(action_id, principal, 'execute', {'approved'}, claim, after=check_run)
        try:
            result = self.connector.execute(action_id, claimed['parameters'])
            status = result.get('status')
            if status not in {'applied', 'failed'}:
                status = 'execution_unknown'
        except Exception:
            result, status = {'message': '外部执行结果未知，请核对状态；禁止盲目重试'}, 'execution_unknown'
        return self._change(action_id, principal, 'execute', {'executing'}, lambda doc: doc.update(status=status, result=result))

    def reconcile(self, action_id, principal):
        document = self.get(action_id, principal)
        authorize(principal, document['asset'], 'execute', document['org'])
        if document['status'] not in {'execution_unknown', 'executing', 'applied'}:
            raise Conflict('当前状态无需核对')
        self._check_connector(document)
        result = self.connector.query(action_id)
        status = result.get('status') if result.get('status') in {'applied', 'failed', 'expired'} else 'execution_unknown'
        return self._change(action_id, principal, 'execute', {document['status']}, lambda doc: doc.update(status=status, result=result, reconciled_at=time.time()))

    def verify(self, action_id, principal, comment):
        document = self.get(action_id, principal)
        authorize(principal, document['asset'], 'review', document['org'])
        self._check_connector(document)
        result = self.connector.query(action_id)
        if result.get('status') != 'applied' or time.time() > document.get('execution_started', 0) + document['parameters']['duration_minutes'] * 60:
            raise Conflict('措施未生效或已过期，不能确认关闭')
        if len(comment.strip()) < 4:
            raise ValueError('请填写回查依据')
        def close_run(connection, action):
            row = connection.execute(select(runs).where(runs.c.id == action['run_id']).with_for_update()).mappings().one()
            if row['status'] != 'reviewed':
                raise Conflict('运行状态已变化，不能关闭')
            state = copy.deepcopy(row['state'])
            state.update(status='completed', reason='人工回查并确认关闭')
            changed = connection.execute(update(runs).where(runs.c.id == action['run_id'], runs.c.version == row['version'])
                .values(state=state, status='completed', version=row['version'] + 1, updated=time.time()))
            if changed.rowcount != 1:
                raise Conflict('运行已更新，请重新核对')
            self.store._event(connection, action['run_id'], 'closed', {'status': 'completed'})
            audit_record(connection, principal, action['asset'], 'run_closed', action['run_id'])
        return self._change(action_id, principal, 'review', {'applied'}, lambda doc: doc.update(status='verified',
            verification={'subject': principal.subject, 'comment': comment, 'at': time.time(), 'external': result}), after=close_run)

    def _check_connector(self, document):
        if document['connector_version'] != self.connector.version or document['simulated'] != self.connector.simulated or document.get('connector_identity') != self._connector_identity():
            raise Conflict('连接器模式或版本已变化，不能核对原操作')

    def _connector_identity(self):
        return parameter_hash({'endpoint': getattr(self.connector, 'base_url', ''), 'version': self.connector.version, 'simulated': self.connector.simulated})
