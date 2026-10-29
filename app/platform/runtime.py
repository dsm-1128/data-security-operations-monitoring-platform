import uuid

from app.agent.loop import AgentLoop
from app.agent.supervisor import MultiAgentSupervisor
from app.agent.model import ModelClient, SimulatedModel
from app.agent.tools import ToolRegistry
from app.tools import TOOL_NAMES
from .auth import authorize
from .connectors import GatewayConnector, SimulatedConnector
from .runs import Conflict
from .config import config_fingerprint
from .evidence import EvidenceArchive
from .telemetry import tracer


def connector_for(settings):
    if settings.connector_mode == 'simulated':
        return SimulatedConnector()
    return GatewayConnector(settings.connector_url, settings.connector_key, settings.connector_version)


def process_run(store, settings, run_id, model_factory=None, registry_factory=None):
    try:
        lease = store.claim(run_id, uuid.uuid4().hex)
    except Conflict:
        return
    state = store.get_internal(run_id)
    try:
        if state.get('config_fingerprint') != config_fingerprint(settings):
            state.update(status='needs_attention', reason='运行配置已变化，请恢复原配置或创建新的调查，禁止混用证据')
            store.save(state, lease)
            return state
        model = model_factory() if model_factory else (SimulatedModel() if settings.model_mode == 'simulated' else ModelClient(settings.model_base_url, settings.model_name, settings.model_key))
        if registry_factory:
            registry = registry_factory()
        elif settings.connector_mode == 'simulated':
            registry = ToolRegistry.demo()
        else:
            gateway = connector_for(settings)
            registry = ToolRegistry({name: lambda query, case, name=name: gateway.read(name, query, case) for name in TOOL_NAMES})
        archive = EvidenceArchive(bucket=settings.attachment_bucket, endpoint=settings.attachment_endpoint)
        registry.archive = lambda evidence: archive.put(run_id, evidence)

        def cancelled():
            current = store.get_internal(run_id)
            if current['status'] != 'running':
                return True
            principal = settings.resolve_principal(state['owner'])
            authorize(principal, state['case']['asset'], 'investigate', state['org'])
            return False

        with tracer().start_as_current_span('agent.run', attributes={'secops.run_id': run_id, 'secops.mode': state['mode']}) as span:
            loop_class = MultiAgentSupervisor if settings.agent_arch == 'multi' else AgentLoop
            result = loop_class(model, registry).run(state, checkpoint=lambda snapshot: store.save(snapshot, lease), cancelled=cancelled)
            span.set_attribute('secops.status', result['status'])
            span.set_attribute('secops.rounds', result['rounds'])
            span.set_attribute('secops.tokens', result['tokens'])
        return result
    except Conflict:
        # Another worker or cancellation owns the run. Never overwrite its state.
        return None
    except Exception:
        state = store.get_internal(run_id)
        state.update(status='needs_attention', reason='运行环境、权限或连接器配置异常，请人工核查')
        try:
            store.save(state, lease)
        except Conflict:
            pass
        return state
