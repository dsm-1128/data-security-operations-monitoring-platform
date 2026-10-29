import hashlib
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.platform.config import Settings
from app.platform.database import Database
from app.platform.sessions import SessionService


def test_oidc_nonce_and_browser_binding_and_identity_mapping(tmp_path):
    identities = tmp_path / 'identities.json'
    identities.write_text(json.dumps({'enterprise-alice': {'org': 'company', 'roles': ['analyst'], 'assets': ['orders']}}))
    settings = Settings(oidc_issuer='https://sso.example', oidc_client_id='secops', oidc_client_secret='secret', identities_file=str(identities))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='test-key', use='sig', alg='RS256')
    claims = {'iss': settings.oidc_issuer, 'aud': 'secops', 'sub': 'enterprise-alice', 'iat': int(time.time()), 'exp': int(time.time()) + 60}
    def provider(request):
        if request.url.path.endswith('openid-configuration'):
            return httpx.Response(200, json={'issuer': settings.oidc_issuer, 'authorization_endpoint': settings.oidc_issuer + '/authorize', 'token_endpoint': settings.oidc_issuer + '/token', 'jwks_uri': settings.oidc_issuer + '/keys'})
        if request.url.path == '/keys':
            return httpx.Response(200, json={'keys': [jwk]})
        return httpx.Response(200, json={'id_token': jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})})
    service = SessionService(Database('sqlite:///' + str(tmp_path / 's.db'), initialize=True), settings, httpx.MockTransport(provider))
    start = service.start_oidc()
    from urllib.parse import parse_qs, urlsplit
    params = parse_qs(urlsplit(start['url']).query)
    assert params['code_challenge_method'] == ['S256']
    with pytest.raises(PermissionError):
        service.finish_oidc(params['state'][0], 'code', 'wrong-browser-cookie')
    claims['nonce'] = 'wrong-nonce'
    with pytest.raises(PermissionError):
        service.finish_oidc(params['state'][0], 'code', start['binding'])
    start = service.start_oidc()
    params = parse_qs(urlsplit(start['url']).query)
    claims['nonce'] = params['nonce'][0]
    token = service.finish_oidc(params['state'][0], 'code', start['binding'])
    assert service.resolve(token).subject == 'enterprise-alice'
    identities.write_text('{}')
    with pytest.raises(PermissionError):
        service.resolve(token)
