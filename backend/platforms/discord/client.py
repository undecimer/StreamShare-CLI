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
        """Discord Webhooks are self-contained and require no active login/auth step."""
        if not self.webhook_url:
            raise AuthenticationError("Discord Webhook URL not provided in credentials")

    async def validate_credentials(self) -> bool:
        """Validate that the Discord Webhook URL is valid by requesting its metadata.
        
        Returns:
            bool: True if webhook metadata is successfully fetched, False otherwise.
        """
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

    def _build_components(self, content: Dict[str, Any], language: str) -> Optional[list]:
        """Build interactive Discord message components (buttons) based on state and available links."""
        vod_url = content.get('vod_url')
        stream_url = content.get('stream_url')
        is_offline = content.get('is_offline', False)
        is_category_change = content.get('is_category_change', False)
        
        button_label = None
        target_url = None

        if is_category_change:
            # During category change, streamer is still live!
            if stream_url:
                button_label = "Ver directo" if language == "es" else "Watch Stream"
                target_url = stream_url
        elif is_offline:
            # When stream has ended:
            if vod_url:
                button_label = "Ver VOD" if language == "es" else "Watch VOD"
                target_url = vod_url
            elif stream_url and "tiktok.com" not in stream_url.lower():
                button_label = "Ver canal" if language == "es" else "Visit Channel"
                target_url = stream_url
        else:
            # Live stream announcement:
            if stream_url:
                button_label = "Ver directo" if language == "es" else "Watch Stream"
                target_url = stream_url

        if button_label and target_url:
            return [
                {
                    "type": 1,  # Action Row
                    "components": [
                        {
                            "type": 2,  # Button
                            "style": 5,  # Link Button
                            "label": button_label,
                            "url": target_url
                        }
                    ]
                }
            ]
        return []

    async def post_content(self, content: Dict[str, Any]) -> str:
        """Post stream status to the Discord Webhook.
        
        Args:
            content: Dictionary containing:
                - text: The message content to send
                - image_path: Path to the 16:9 overlay image (optional)
                - stream_url: URL to the stream to attach as a Link Button (optional)
                - vod_url: Direct URL to the recorded VOD (optional)
                - is_category_change: Whether this is a segment change while still live
                - is_offline: Whether the stream has completely ended
                
        Returns:
            str: The message ID created by Discord.
        """
        await self.authenticate()
        
        text = content.get('text', '')
        embeds = content.get('embeds')
        image_path = content.get('image_path')
        language = content.get('language', 'en')
        
        url = f"{self.webhook_url}?wait=true&with_components=true"
        
        # Build payload
        payload = {"content": text}
        if embeds:
            payload["embeds"] = embeds
            
        components = self._build_components(content, language)
        if components:
            payload["components"] = components
        
        try:
            import json
            async with aiohttp.ClientSession() as session:
                if image_path and os.path.exists(image_path):
                    data = aiohttp.FormData()
                    # Add payload as json string
                    data.add_field('payload_json', json.dumps(payload))
                    # Open and add image file
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
                    # Simple text/embed post
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
        """Update a previously posted Discord message using the webhook.
        
        Args:
            message_id: The ID of the message to update.
            content: Dictionary containing text, embeds, components, image_path, etc.
            
        Returns:
            bool: True if successfully updated, False otherwise.
        """
        await self.authenticate()
        
        text = content.get('text', '')
        embeds = content.get('embeds')
        image_path = content.get('image_path')
        language = content.get('language', 'en')
        
        url = f"{self.webhook_url}/messages/{message_id}?wait=true&with_components=true"
        
        payload = {"content": text}
        if embeds:
            payload["embeds"] = embeds
            
        payload["components"] = self._build_components(content, language)
            
        try:
            import json
            async with aiohttp.ClientSession() as session:
                if image_path and os.path.exists(image_path):
                    # We are replacing the image attachment
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
                    # Simple JSON update
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
        """Delete a previously posted message from Discord using its message ID.
        
        Args:
            message_id: The ID of the message to delete.
            
        Returns:
            bool: True if successfully deleted, False otherwise.
        """
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
