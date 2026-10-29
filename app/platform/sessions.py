import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import delete, insert, select

from .connectors import bounded_json
from .database import sessions


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SessionService:
    def __init__(self, db, settings, transport=None):
        self.db, self.settings, self.transport = db, settings, transport

    def issue(self, subject, ttl=3600):
        self.settings.resolve_principal(subject)
        token = secrets.token_urlsafe(32)
        with self.db.engine.begin() as connection:
            connection.execute(delete(sessions).where(sessions.c.expires < time.time()))
            connection.execute(insert(sessions).values(token_hash=digest(token), subject=subject, expires=time.time() + ttl, kind='session', document={}))
        return token

    def resolve(self, token):
        with self.db.engine.connect() as connection:
            row = connection.execute(select(sessions).where(sessions.c.token_hash == digest(token), sessions.c.kind == 'session', sessions.c.expires > time.time())).mappings().first()
        if not row:
            raise PermissionError('请登录，或会话已失效')
        return self.settings.resolve_principal(row['subject'])

    def revoke(self, token):
        with self.db.engine.begin() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == digest(token)))

    def _metadata(self):
        issuer = self.settings.oidc_issuer.rstrip('/')
        if not issuer:
            raise PermissionError('尚未配置企业 SSO')
        with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False, trust_env=False) as client:
            metadata = bounded_json(client, 'GET', issuer + '/.well-known/openid-configuration')
        if metadata.get('issuer') != self.settings.oidc_issuer:
            raise PermissionError('OIDC issuer 不匹配')
        for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            parsed = urlsplit(metadata[name])
            if parsed.scheme != 'https' or parsed.netloc != urlsplit(issuer).netloc:
                raise PermissionError('OIDC 端点不属于配置的受信任来源')
        return metadata

    def start_oidc(self):
        metadata = self._metadata()
        state, nonce, verifier, binding = [secrets.token_urlsafe(32) for _ in range(4)]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with self.db.engine.begin() as connection:
            connection.execute(delete(sessions).where(sessions.c.expires < time.time()))
            connection.execute(insert(sessions).values(token_hash=digest(state), subject='oidc', kind='oidc', expires=time.time() + 300,
                document={'nonce': nonce, 'verifier': verifier, 'binding': digest(binding)}))
        parameters = {'client_id': self.settings.oidc_client_id, 'response_type': 'code', 'scope': 'openid profile',
            'redirect_uri': self.settings.public_url.rstrip('/') + '/auth/callback', 'state': state, 'nonce': nonce,
            'code_challenge': challenge, 'code_challenge_method': 'S256'}
        return {'url': metadata['authorization_endpoint'] + '?' + urlencode(parameters), 'binding': binding}

    def finish_oidc(self, state, code, binding):
        with self.db.engine.begin() as connection:
            row = connection.execute(select(sessions).where(sessions.c.token_hash == digest(state), sessions.c.kind == 'oidc', sessions.c.expires > time.time()).with_for_update()).mappings().first()
            if not row or not secrets.compare_digest(row['document']['binding'], digest(binding)):
                raise PermissionError('无效的登录状态或浏览器绑定')
            result = connection.execute(delete(sessions).where(sessions.c.token_hash == digest(state)))
            if result.rowcount != 1:
                raise PermissionError('登录请求已使用')
        metadata = self._metadata()
        try:
            with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False, trust_env=False) as client:
                tokens = bounded_json(client, 'POST', metadata['token_endpoint'], data={'grant_type': 'authorization_code', 'code': code,
                    'client_id': self.settings.oidc_client_id, 'client_secret': self.settings.oidc_client_secret,
                    'redirect_uri': self.settings.public_url.rstrip('/') + '/auth/callback', 'code_verifier': row['document']['verifier']})
                keys = bounded_json(client, 'GET', metadata['jwks_uri'])['keys']
            header = jwt.get_unverified_header(tokens['id_token'])
            key = next(jwt.PyJWK(k).key for k in keys if k.get('kid') == header.get('kid') and k.get('kty') == 'RSA' and k.get('use', 'sig') == 'sig')
            claims = jwt.decode(tokens['id_token'], key, algorithms=['RS256'], audience=self.settings.oidc_client_id,
                issuer=self.settings.oidc_issuer, options={'require': ['exp', 'iat', 'sub', 'iss', 'aud', 'nonce']})
            if not secrets.compare_digest(claims['nonce'], row['document']['nonce']):
                raise ValueError('nonce')
            if claims.get('azp', self.settings.oidc_client_id) != self.settings.oidc_client_id:
                raise ValueError('azp')
            if isinstance(claims['aud'], list) and len(claims['aud']) > 1 and claims.get('azp') != self.settings.oidc_client_id:
                raise ValueError('audience')
        except Exception as exc:
            raise PermissionError('企业登录令牌验证失败') from exc
        return self.issue(claims['sub'], ttl=min(3600, max(1, claims['exp'] - time.time())))
