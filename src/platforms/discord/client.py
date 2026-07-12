import aiohttp
import os
import logging
from typing import Dict, Any, Optional
from ..base import BasePlatform
from ...core.exceptions import AuthenticationError, PlatformError

logger = logging.getLogger(__name__)

class DiscordWebhookClient(BasePlatform):
    def __init__(self, account_id: Optional[str] = None, credentials: Dict[str, Any] = None):
        super().__init__(account_id)
        self.credentials = credentials or {}
        self.webhook_url = self.credentials.get('webhook_url')

    async def authenticate(self) -> None:
        """Discord Webhooks require no active login/auth step."""
        if not self.webhook_url:
            raise AuthenticationError("Discord Webhook URL not provided in credentials")

    async def validate_credentials(self) -> bool:
        """Validate that the Discord Webhook URL is valid."""
        if not self.webhook_url or not self.webhook_url.startswith("https://discord.com/api/webhooks/"):
            logger.error("Invalid Discord webhook URL format")
            return False
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.webhook_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return "id" in data and "token" in data
                    else:
                        logger.error(f"Discord webhook validation failed with status code {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Error validating Discord webhook: {e}")
            return False

    async def post_content(self, content: Dict[str, Any]) -> str:
        """Post stream status to the Discord Webhook.
        
        Args:
            content: Dictionary containing:
                - text: The message content to send
                - image_path: Path to the 16:9 overlay image (optional)
                - stream_url: URL to the stream to attach as a Link Button (optional)
                
        Returns:
            str: The message ID created by Discord.
        """
        await self.authenticate()
        
        text = content.get('text', '')
        embeds = content.get('embeds')
        image_path = content.get('image_path')
        stream_url = content.get('stream_url')
        language = content.get('language', 'en')
        
        url = f"{self.webhook_url}?wait=true&with_components=true"
        
        payload = {"content": text}
        if embeds:
            payload["embeds"] = embeds
            
        if stream_url:
            button_label = "Ver transmisión" if language == "es" else "Watch Stream"
            if content.get('is_offline'):
                button_label = "Ver VOD" if language == "es" else "Watch VOD"
            payload["components"] = [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 5,
                            "label": button_label,
                            "url": stream_url
                        }
                    ]
                }
            ]
        
        try:
            import json
            async with aiohttp.ClientSession() as session:
                if image_path and os.path.exists(image_path):
                    data = aiohttp.FormData()
                    data.add_field('payload_json', json.dumps(payload))
                    with open(image_path, 'rb') as f:
                        data.add_field(
                            'files[0]', 
                            f.read(), 
                            filename=os.path.basename(image_path), 
                            content_type='image/png' if image_path.endswith('.png') else 'image/jpeg'
                        )
                    async with session.post(url, data=data) as resp:
                        if resp.status in (200, 201, 204):
                            res_data = await resp.json()
                            return str(res_data['id'])
                        else:
                            err_text = await resp.text()
                            raise PlatformError(f"Discord Webhook post failed: status={resp.status}, response={err_text}")
                else:
                    async with session.post(url, json=payload) as resp:
                        if resp.status in (200, 201, 204):
                            res_data = await resp.json()
                            return str(res_data['id'])
                        else:
                            err_text = await resp.text()
                            raise PlatformError(f"Discord Webhook post failed: status={resp.status}, response={err_text}")
        except Exception as e:
            if isinstance(e, PlatformError):
                raise
            logger.error(f"Exception in Discord post_content: {e}")
            raise PlatformError(f"Failed to post to Discord: {e}")

    async def update_content(self, message_id: str, content: Dict[str, Any]) -> bool:
        """Update a previously posted Discord message using the webhook."""
        await self.authenticate()
        
        text = content.get('text', '')
        embeds = content.get('embeds')
        image_path = content.get('image_path')
        stream_url = content.get('stream_url')
        language = content.get('language', 'en')
        
        url = f"{self.webhook_url}/messages/{message_id}?wait=true&with_components=true"
        
        payload = {"content": text}
        if embeds:
            payload["embeds"] = embeds
            
        if stream_url:
            button_label = "Ver transmisión" if language == "es" else "Watch Stream"
            if content.get('is_offline'):
                button_label = "Ver VOD" if language == "es" else "Watch VOD"
            payload["components"] = [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 5,
                            "label": button_label,
                            "url": stream_url
                        }
                    ]
                }
            ]
            
        try:
            import json
            async with aiohttp.ClientSession() as session:
                if image_path and os.path.exists(image_path):
                    payload["attachments"] = []
                    data = aiohttp.FormData()
                    data.add_field('payload_json', json.dumps(payload))
                    with open(image_path, 'rb') as f:
                        data.add_field(
                            'files[0]', 
                            f.read(), 
                            filename=os.path.basename(image_path), 
                            content_type='image/png' if image_path.endswith('.png') else 'image/jpeg'
                        )
                    async with session.patch(url, data=data) as resp:
                        if resp.status in (200, 201, 204):
                            return True
                        else:
                            err_text = await resp.text()
                            logger.error(f"Discord Webhook edit failed: status={resp.status}, response={err_text}")
                            return False
                else:
                    async with session.patch(url, json=payload) as resp:
                        if resp.status in (200, 201, 204):
                            return True
                        else:
                            err_text = await resp.text()
                            logger.error(f"Discord Webhook edit failed: status={resp.status}, response={err_text}")
                            return False
        except Exception as e:
            logger.error(f"Exception in Discord update_content: {e}")
            return False

    async def delete_post(self, message_id: str) -> bool:
        """Delete a previously posted message from Discord using its message ID."""
        if not self.webhook_url:
            logger.error("No Discord Webhook URL available to delete message")
            return False
            
        url = f"{self.webhook_url}/messages/{message_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(url) as resp:
                    if resp.status == 204:
                        logger.info(f"Successfully deleted Discord message {message_id}")
                        return True
                    else:
                        err_text = await resp.text()
                        logger.error(f"Failed to delete Discord message {message_id}: status={resp.status}, response={err_text}")
                        return False
        except Exception as e:
            logger.error(f"Exception while deleting Discord message {message_id}: {e}")
            return False
