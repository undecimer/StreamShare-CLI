"""Local destination authorization for the standalone CLI.

In the ecosystem, every publish destination is authorized against a OneSite
grant via an injected ``destination_authorizer``. The open-source CLI is
single-user: the accounts in the local ``.env`` / config are the authority,
so locally configured accounts are always allowed. The grant check still
requires each account entry to carry a ``connection_id`` (env-loaded
accounts get ``'local'`` in ``core.config``).
"""

from typing import Any, Dict


async def local_destination_authorizer(platform_key: str, account_id: str, connection_id: str) -> Dict[str, Any]:
    return {"allowed": True, "connection_id": connection_id}
