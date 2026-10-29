from dataclasses import asdict, dataclass


PERMISSIONS = {
    'analyst': {'read', 'investigate', 'review', 'request', 'cancel'},
    'manager': {'read', 'investigate', 'review', 'request', 'approve', 'cancel'},
    'operator': {'read', 'execute'},
    'auditor': {'read', 'audit'},
    'admin': {'read', 'configure', 'audit'},
}


@dataclass(frozen=True)
class Principal:
    subject: str
    org: str
    roles: tuple[str, ...]
    assets: tuple[str, ...]

    def as_dict(self):
        return asdict(self)


def authorize(principal, asset, permission, org=None):
    allowed = set().union(*(PERMISSIONS.get(role, set()) for role in principal.roles))
    if permission not in allowed or (org and principal.org != org) or ('*' not in principal.assets and asset not in principal.assets):
        raise PermissionError('当前身份无权访问该资产或执行此操作')


def demo_principal(role):
    if role not in PERMISSIONS:
        raise PermissionError('无效演示角色')
    return Principal('demo-' + role, 'demo', (role,), ('*',))
