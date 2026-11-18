from typing import Any, Dict, Optional
from .client import DiscordWebhookClient


def discord_client(account_id: Optional[str], credentials: Optional[Dict[str, Any]]):
    """Resolve the Discord delivery lane for an account.

    The open-source CLI only ships the webhook lane.
    """
    return DiscordWebhookClient(account_id, credentials)
