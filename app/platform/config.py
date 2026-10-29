import json
import os
import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .auth import Principal, demo_principal


def secret(name):
    path = os.getenv(name + '_FILE')
    return Path(path).read_text(encoding='utf-8').strip() if path else os.getenv(name, '')


def config_fingerprint(settings):
    values = [settings.mode, settings.model_mode, settings.model_base_url, settings.model_name,
              settings.connector_mode, settings.connector_url, settings.connector_version,
              settings.attachment_bucket, settings.attachment_endpoint]
    # Preserve legacy single snapshots; multi snapshots must never share that identity.
    if settings.agent_arch != 'single':
        values.append(settings.agent_arch)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


@dataclass
class Settings:
    mode: str = 'development'
    database_url: str = 'sqlite:///data/platform.sqlite3'
    redis_url: str = 'redis://127.0.0.1:6379/0'
    dispatch: str = 'inline'
    model_mode: str = 'simulated'
    model_base_url: str = ''
    model_name: str = ''
    model_key: str = ''
    connector_mode: str = 'simulated'
    connector_url: str = ''
    connector_key: str = ''
    connector_version: str = 'v1'
    public_url: str = 'http://127.0.0.1:18081'
    oidc_issuer: str = ''
    oidc_client_id: str = ''
    oidc_client_secret: str = ''
    identities_file: str = ''
    allowed_hosts: str = '127.0.0.1,localhost,testserver'
    attachment_bucket: str = ''
    attachment_endpoint: str = ''
    max_rounds: int = 12
    max_tools: int = 24
    max_seconds: int = 300
    max_tokens: int = 64000
    agent_arch: str = 'single'

    def budgets(self):
        return {name: getattr(self, name) for name in ('max_rounds', 'max_tools', 'max_seconds', 'max_tokens')}

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('SECOPS_MODE', 'development'), database_url=secret('SECOPS_DATABASE_URL') or 'sqlite:///data/platform.sqlite3',
            redis_url=secret('SECOPS_REDIS_URL') or 'redis://127.0.0.1:6379/0', dispatch=os.getenv('SECOPS_DISPATCH', 'inline'),
            agent_arch=os.getenv('SECOPS_AGENT_ARCH', 'single'),
            model_mode=os.getenv('SECOPS_MODEL_MODE', 'simulated'), model_base_url=os.getenv('SECOPS_LLM_BASE_URL', ''),
            model_name=os.getenv('SECOPS_LLM_MODEL', ''), model_key=secret('SECOPS_LLM_API_KEY'),
            connector_mode=os.getenv('SECOPS_CONNECTOR_MODE', 'simulated'), connector_url=os.getenv('SECOPS_CONNECTOR_URL', ''),
            connector_key=secret('SECOPS_CONNECTOR_TOKEN'), connector_version=os.getenv('SECOPS_CONNECTOR_VERSION', 'v1'),
            public_url=os.getenv('SECOPS_PUBLIC_URL', 'http://127.0.0.1:18081'), oidc_issuer=os.getenv('SECOPS_OIDC_ISSUER', ''),
            oidc_client_id=os.getenv('SECOPS_OIDC_CLIENT_ID', ''), oidc_client_secret=secret('SECOPS_OIDC_CLIENT_SECRET'),
            identities_file=os.getenv('SECOPS_IDENTITIES_FILE', ''), allowed_hosts=os.getenv('SECOPS_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver'),
            attachment_bucket=os.getenv('SECOPS_ATTACHMENT_BUCKET', ''), attachment_endpoint=os.getenv('SECOPS_ATTACHMENT_ENDPOINT', ''),
            max_rounds=int(os.getenv('SECOPS_MAX_ROUNDS', '12')), max_tools=int(os.getenv('SECOPS_MAX_TOOLS', '24')),
            max_seconds=int(os.getenv('SECOPS_MAX_SECONDS', '300')), max_tokens=int(os.getenv('SECOPS_MAX_TOKENS', '64000')))

    def validate_startup(self):
        if self.agent_arch not in {'single', 'multi'}:
            raise ValueError('无效 Agent 架构，必须为 single 或 multi')
        if not (1 <= self.max_rounds <= 48 and 1 <= self.max_tools <= 96 and 1 <= self.max_seconds <= 300 and 1024 <= self.max_tokens <= 1000000):
            raise ValueError('预算必须在轮次 1–48、工具 1–96、秒数 1–300、Token 1024–1000000 范围内')
        if self.mode not in {'development', 'production'} or self.dispatch not in {'inline', 'celery'}:
            raise ValueError('无效部署模式或调度方式')
        if self.model_mode not in {'simulated', 'live'} or self.connector_mode not in {'simulated', 'gateway'}:
            raise ValueError('无效模型或连接器模式')
        if self.model_mode == 'live' and not all([self.model_base_url, self.model_name, self.model_key]):
            raise ValueError('真实模型模式需要完整模型配置')
        if self.connector_mode == 'gateway' and not all([self.connector_url, self.connector_key]):
            raise ValueError('真实连接器模式需要地址与凭据')
        for address in [self.model_base_url, self.connector_url, self.public_url, self.oidc_issuer]:
            if address and (urlsplit(address).scheme not in {'http', 'https'} or urlsplit(address).username or urlsplit(address).password or urlsplit(address).fragment):
                raise ValueError('服务地址必须为不含凭据的 HTTP(S) URL')
        if self.mode == 'production':
            if not self.database_url.startswith('mysql+pymysql://') or self.dispatch != 'celery' or self.model_mode != 'live' or self.connector_mode != 'gateway':
                raise ValueError('production 需要 MySQL、Celery、真实模型与真实连接器')
            if not all([self.oidc_issuer, self.oidc_client_id, self.oidc_client_secret, self.identities_file]):
                raise ValueError('production 需要企业 OIDC 与身份授权映射')
            if any(urlsplit(u).scheme != 'https' for u in [self.model_base_url, self.connector_url, self.public_url, self.oidc_issuer]):
                raise ValueError('production 外部接口与公开入口必须使用 HTTPS')
            if '*' in self.allowed_hosts.split(','):
                raise ValueError('production 必须限定 Host')
            json.loads(Path(self.identities_file).read_text(encoding='utf-8'))

    def resolve_principal(self, subject):
        if self.mode == 'development' and subject.startswith('demo-'):
            return demo_principal(subject.removeprefix('demo-'))
        if not self.identities_file:
            raise PermissionError('没有配置企业身份映射')
        records = json.loads(Path(self.identities_file).read_text(encoding='utf-8'))
        row = records.get(subject)
        if not row or not row.get('enabled', True):
            raise PermissionError('身份未授权或已停用')
        return Principal(subject, row['org'], tuple(row['roles']), tuple(row['assets']))
