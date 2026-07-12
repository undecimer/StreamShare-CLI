from instagrapi import Client
from instagrapi.types import StorySticker, StoryStickerLink, StoryLink
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError, ConfigError
from ..base import BasePlatform
from typing import Dict, Any, Optional
import os
import tempfile
import logging
import uuid
import json

logger = logging.getLogger(__name__)

class InstagramClient(BasePlatform):
    def __init__(self, account_id: Optional[str] = None, credentials: Dict[str, Any] = None):
        super().__init__(account_id)
        
        if credentials:
            self.config = credentials
        else:
            config = Config()
            accounts = config.get('instagram_accounts')
            if not accounts:
                raise ConfigError("No Instagram accounts configured")
            
            if self.account_id not in accounts:
                raise ConfigError(f"Instagram account '{self.account_id}' not found")
                
            self.config = accounts[self.account_id]
            
        if not self.config.get('enabled', True):
            raise ConfigError(f"Instagram account '{self.account_id}' is disabled")
            
        self.client = Client()
        
        # Generate deterministic device settings based on username if available
        username = self.config.get('username')
        if username:
            import hashlib
            seed = hashlib.md5(username.encode()).hexdigest()
            self.client.android_device_id = "android-%s" % seed[:16]
            self.client.uuid = str(uuid.UUID(hashlib.md5(seed.encode()).hexdigest()))
            self.client.phone_id = str(uuid.UUID(hashlib.md5((seed + "phone").encode()).hexdigest()))
            logger.info(f"Initialized Instagram client with deterministic device ID for user: {username}")
            
        self._authenticated = False

    def complete_2fa_login(self, verification_code: str) -> Dict[str, Any]:
        """Complete 2FA login using the existing session state and a provided code."""
        try:
            if not hasattr(self.client, 'last_json') or not self.client.last_json:
                raise AuthenticationError("No pending 2FA challenge found")
                
            two_factor_info = self.client.last_json.get("two_factor_info", {})
            two_factor_identifier = two_factor_info.get("two_factor_identifier")
            
            if not two_factor_identifier:
                raise AuthenticationError("Missing 2FA identifier")

            if not verification_code:
                logger.info("No code provided, checking if notification was approved...")
                try:
                    self.client.get_timeline_feed()
                    logger.info("Notification approval verified!")
                    self._authenticated = True
                    return self.client.get_settings()
                except Exception as e:
                    logger.warning(f"Notification check failed: {e}")
                    raise AuthenticationError("Login not approved yet. Please approve the notification on your phone and try again.")

            from uuid import uuid4
            data = {
                "verification_code": verification_code,
                "phone_id": self.client.phone_id,
                "_csrftoken": self.client.token,
                "two_factor_identifier": two_factor_identifier,
                "username": self.config['username'],
                "trust_this_device": "0",
                "guid": self.client.uuid,
                "device_id": self.client.android_device_id,
                "waterfall_id": str(uuid4()),
                "verification_method": "1" 
            }

            self.client.private_request("accounts/two_factor_login/", data, login=True)
            self._authenticated = True
            return self.client.get_settings()
            
        except Exception as e:
            logger.error(f"Failed to complete 2FA login: {e}")
            raise AuthenticationError(f"2FA failed: {e}")

    def login(self, verification_code: Optional[str] = None) -> Dict[str, Any]:
        """Attempt to login with credentials and optional verification code."""
        try:
            code_to_use = verification_code or ""
            self.client.login(self.config['username'], self.config['password'], verification_code=code_to_use)
            self._authenticated = True
            return self.client.get_settings()
        except Exception as e:
            logger.error(f"Instagram login failed: {e}")
            
            is_2fa_required = False
            two_factor_info = None
            
            try:
                if hasattr(self.client, 'last_response'):
                    resp_json = self.client.last_response.json()
                    if resp_json.get('two_factor_required'):
                        is_2fa_required = True
                        two_factor_info = resp_json.get('two_factor_info', {})
                        logger.info(f"2FA required detected. Full response: {resp_json}")
            except Exception as parse_e:
                logger.warning(f"Failed to parse 2FA response: {parse_e}")
                pass
                
            error_msg = str(e)
            if is_2fa_required or 'Two-factor authentication required' in error_msg:
                # If we have a TOTP secret, try to generate code automatically
                totp_secret = self.config.get('totp_secret')
                if totp_secret and not verification_code:
                    try:
                        import pyotp
                        totp = pyotp.TOTP(totp_secret)
                        code = totp.now()
                        logger.info(f"Generated 2FA code for account '{self.account_id}'")
                        return self.login(verification_code=code)
                    except Exception as totp_e:
                        logger.error(f"Failed to generate 2FA code: {totp_e}")
                
                if not verification_code and two_factor_info:
                    try:
                        if two_factor_info.get('pending_trusted_notification'):
                            logger.info("Pending trusted notification detected. Approve via notification on phone.")
                        else:
                            identifier = two_factor_info.get('two_factor_identifier')
                            if identifier:
                                logger.info(f"Triggering 2FA SMS for identifier: {identifier}")
                                data = {
                                    "two_factor_identifier": identifier,
                                    "username": self.config['username'],
                                    "device_id": self.client.android_device_id,
                                    "guid": self.client.uuid,
                                    "verification_method": "1"
                                }
                                resp = self.client.private_request(
                                    "accounts/send_two_factor_login_sms/",
                                    data
                                )
                                logger.info(f"Triggered 2FA SMS response: {resp}")
                    except Exception as sms_e:
                        logger.warning(f"Failed to trigger 2FA SMS: {sms_e}")
                
                hint_parts = []
                if two_factor_info:
                    if two_factor_info.get('pending_trusted_notification'):
                        hint_parts.append("Approve the login notification on your phone")
                    if two_factor_info.get('sms_two_factor_on'):
                        phone = two_factor_info.get('obfuscated_phone_number', 'your number')
                        hint_parts.append(f"check SMS ({phone})")
                    if two_factor_info.get('whatsapp_two_factor_on'):
                        phone = two_factor_info.get('obfuscated_phone_number', 'your number')
                        hint_parts.append(f"check WhatsApp ({phone})")
                    if two_factor_info.get('totp_two_factor_on'):
                        hint_parts.append("use your authenticator app")
                    if two_factor_info.get('obfuscated_email'):
                        email = two_factor_info.get('obfuscated_email', 'your email')
                        hint_parts.append(f"check email ({email})")

                hint = " / ".join(hint_parts) if hint_parts else "Check your authentication app or SMS"
                
                # If running interactively in terminal, prompt user for code
                print(f"\n[Instagram 2FA Required]: {hint}")
                code = input("Please enter the 2FA verification code: ")
                if code:
                    return self.complete_2fa_login(code.strip())
                raise AuthenticationError("2FA_REQUIRED", details={"hint": hint})
            elif "challenge_required" in error_msg:
                raise AuthenticationError("CHALLENGE_REQUIRED")
            elif "bad_password" in error_msg or "invalid_user" in error_msg:
                raise AuthenticationError("INVALID_CREDENTIALS")
            else:
                raise AuthenticationError(f"Login failed: {error_msg}")

    async def authenticate(self) -> Dict[str, Any]:
        """Authenticate with Instagram using session management and 2FA."""
        session_file = self.get_session_file('instagram_session.json')
        session_settings = None

        if os.path.exists(session_file):
            try:
                with open(session_file, 'r') as f:
                    session_settings = json.load(f)
                logger.info(f"Loaded Instagram session settings from: {session_file}")
            except Exception as e:
                logger.warning(f"Failed to read session file {session_file}: {e}")

        try:
            if session_settings:
                self.client.set_settings(session_settings)
                self.client.login(self.config['username'], self.config['password'])
                self.client.get_timeline_feed() # Validate session
                logger.info(f"Successfully logged in via session for account '{self.account_id}'")
                self._authenticated = True
                return self.client.get_settings()
        except Exception as e:
            logger.warning(f"Session login failed for account '{self.account_id}': {e}. Retrying with credentials.")
            self.client = Client()

        # Fallback to credential login
        settings = self.login()
        
        # Save successfully authenticated session settings
        try:
            with open(session_file, 'w') as f:
                json.dump(settings, f)
            logger.info(f"Saved new Instagram session settings to: {session_file}")
        except Exception as e:
            logger.warning(f"Failed to save session settings to file: {e}")

        return settings

    async def validate_credentials(self) -> bool:
        """Validate that the current credentials are valid"""
        try:
            if not self._authenticated:
                await self.authenticate()
            return True
        except Exception:
            return False

    async def post_content(self, content: Dict[str, Any]) -> str:
        """Post content to Instagram Stories
        
        Args:
            content: Dictionary containing content details
                    Required keys: image_path, caption, url
        
        Returns:
            str: Story media ID
        """
        if not self._authenticated:
            await self.authenticate()
            
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                temp_path = temp_file.name
                
            with open(content['image_path'], 'rb') as src, open(temp_path, 'wb') as dst:
                dst.write(src.read())
            
            story_link = StoryLink(
                webUri=content['url'],
                x=0.5,
                y=0.7,
                z=1,
                width=1,
                height=1,
                rotation=0.0
            )

            story_sticker = StorySticker(
                type="link",
                x=0.5,
                y=0.7,
                z=1,
                width=1,
                height=1,
                rotation=0.0,
                story_link=StoryStickerLink(
                    url=content['url'],
                    link_title="Ver directo",
                    link_type="web",
                    display_url=content['url']
                )
            )
            
            story = self.client.photo_upload_to_story(
                temp_path,
                links=[story_link],
                stickers=[story_sticker]
            )
            
            os.unlink(temp_path)
            logger.info(f"Successfully posted to Instagram Stories for account '{self.account_id}'")
            return str(story.pk)
            
        except Exception as e:
            raise PlatformError(f"Failed to post to Instagram Story for account '{self.account_id}': {str(e)}")

    async def delete_story(self, media_id: str) -> None:
        """Delete a story by its media ID"""
        if not self._authenticated:
            await self.authenticate()
            
        try:
            story_pk = int(media_id)
            self.client.story_delete(story_pk)
            logger.info(f"Successfully deleted Instagram Story {media_id} for account '{self.account_id}'")
        except Exception as e:
            raise PlatformError(f"Failed to delete Instagram Story {media_id} for account '{self.account_id}': {str(e)}")
