from instagrapi import Client
from instagrapi.types import Hashtag, StoryHashtag, StoryLink, StoryMention, UserShort
from instagrapi.exceptions import (
    BadCredentials,
    BadPassword,
    ChallengeRequired,
    ClientError,
    ClientThrottledError,
    PleaseWaitFewMinutes,
    RateLimitError,
    ReloginAttemptExceeded,
    TwoFactorRequired,
)
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError, ConfigError
from ..base import BasePlatform
from typing import Dict, Any, List, Optional
import asyncio
import os
import re
import tempfile
import logging

logger = logging.getLogger(__name__)


class InstagramLoginError(AuthenticationError):
    code = 'instagram_login_failed'


class InstagramInvalidCredentials(InstagramLoginError):
    code = 'invalid_credentials'


class InstagramVerificationRequired(InstagramLoginError):
    code = 'verification_required'


class InstagramManualVerificationRequired(InstagramLoginError):
    code = 'manual_verification_required'


class InstagramRateLimited(InstagramLoginError):
    code = 'rate_limited'


class InstagramSessionExpired(InstagramLoginError):
    code = 'session_expired'


_THROTTLE_ERRORS = (PleaseWaitFewMinutes, ClientThrottledError, RateLimitError, ReloginAttemptExceeded)

_TWO_FACTOR_KEYS = {
    'two_step_verification_context',
    'sms_two_factor_on',
    'totp_two_factor_on',
    'obfuscated_phone_number',
    'obfuscated_email',
}


def _deep_find(source: Any, keys: set, found: Dict[str, Any], depth: int = 0) -> None:
    if depth > 5:
        return
    if isinstance(source, dict):
        for k, v in source.items():
            key = str(k).lower()
            if key in keys and v is not None and key not in found:
                found[key] = v
            _deep_find(v, keys, found, depth + 1)
    elif isinstance(source, (list, tuple)):
        for item in source:
            _deep_find(item, keys, found, depth + 1)


def _extract_two_factor_data(exc: Exception) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    for name, value in vars(exc).items():
        if name.startswith('_'):
            continue
        key = name.lower()
        if key in _TWO_FACTOR_KEYS and value is not None:
            found[key] = value
            continue
        _deep_find(value, _TWO_FACTOR_KEYS, found)
    return found


class InstagramClient(BasePlatform):
    def __init__(self, account_id: Optional[str] = None, credentials: Dict[str, Any] = None):
        super().__init__(account_id)

        if credentials is not None:
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

        session_settings = self.config.get('session_settings')
        if not session_settings:
            from ...core.session_store import load_session
            session_settings = load_session(self.account_id or 'default')
            if session_settings:
                self.config['session_settings'] = session_settings
        self.client = Client(settings=session_settings or None, with_challenge_flow=False)
        self._authenticated = False
        if self.config.get('password'):
            self._auth_method: Optional[str] = 'password'
        elif session_settings or self.config.get('sessionid'):
            self._auth_method = 'sessionid'
        else:
            self._auth_method = None
        self._username: Optional[str] = None
        self._verification_details: Dict[str, Any] = {}
        self.two_step_verification_context: Optional[str] = None
        self._story_user_cache: Dict[str, Any] = {}
        self._story_hashtag_cache: Dict[str, Any] = {}

    @property
    def username(self) -> Optional[str]:
        return self._username or self.config.get('username')

    def storage_credentials(self) -> Dict[str, Any]:
        credentials: Dict[str, Any] = {
            'username': self.username,
            'session_settings': self.client.get_settings(),
        }
        if self._auth_method == 'password' and self.config.get('password'):
            credentials['password'] = self.config['password']
        return credentials

    def _resolve_authenticated_username(self) -> str:
        try:
            account = self.client.account_info()
        except _THROTTLE_ERRORS as exc:
            raise InstagramRateLimited(
                "Instagram is temporarily rate limiting logins. Please wait and try again later."
            ) from exc
        except ChallengeRequired as exc:
            raise InstagramManualVerificationRequired(
                "Instagram requires manual verification. Confirm this login in the official "
                "Instagram app or website on a trusted device, then retry."
            ) from exc
        except Exception as exc:
            logger.warning("Instagram account lookup failed (%s)", type(exc).__name__)
            raise InstagramLoginError(
                "Instagram did not confirm the authenticated account. Please try again."
            ) from exc
        username = str(getattr(account, 'username', '') or '').strip()
        if not username:
            raise InstagramLoginError(
                "Instagram did not confirm the authenticated account. Please try again."
            )
        return username

    def _build_verification_error(self, exc: TwoFactorRequired) -> InstagramVerificationRequired:
        data = _extract_two_factor_data(exc)

        context = data.get('two_step_verification_context')
        if context:
            self.two_step_verification_context = context

        methods = []
        if data.get('totp_two_factor_on'):
            methods.append('authenticator')
        if data.get('sms_two_factor_on'):
            methods.append('sms')
        if not any(method in methods for method in ('authenticator', 'sms')):
            methods.append('authenticator')
        methods.append('backup_code')

        phone = data.get('obfuscated_phone_number')
        email = data.get('obfuscated_email')
        hint_parts = []
        if data.get('totp_two_factor_on') or 'authenticator' in methods:
            hint_parts.append("use your authenticator app")
        if data.get('sms_two_factor_on'):
            hint_parts.append(f"check SMS ({phone})" if phone else "check SMS")
        if email:
            hint_parts.append(f"check email ({email})")
        hint = " / ".join(hint_parts) or (
            "Enter the 6-digit code from your authenticator app or SMS, or an 8-digit backup code"
        )

        details = {"hint": hint, "methods": methods}
        self._verification_details = details
        return InstagramVerificationRequired(
            "Instagram requires a verification code",
            details=details,
        )

    def _verification_pending(self) -> InstagramVerificationRequired:
        details = self._verification_details or {
            "hint": "Enter the 6-digit code from your authenticator app or SMS, or an 8-digit backup code",
            "methods": ["authenticator", "backup_code"],
        }
        return InstagramVerificationRequired(
            "Instagram still requires a verification code",
            details={"hint": details["hint"], "methods": list(details["methods"])},
        )

    def login(self, verification_code: Optional[str] = None) -> Dict[str, Any]:
        clean_code = str(verification_code or '').strip()
        username = self.config['username']
        password = self.config['password']

        try:
            logged_in = self.client.login(username, password, verification_code=clean_code)
        except TwoFactorRequired as exc:
            raise self._build_verification_error(exc) from exc
        except (BadPassword, BadCredentials) as exc:
            raise InstagramInvalidCredentials("Instagram rejected the username or password") from exc
        except ChallengeRequired as exc:
            raise InstagramManualVerificationRequired(
                "Instagram requires manual verification. Confirm this login in the official "
                "Instagram app or website on a trusted device, then retry."
            ) from exc
        except _THROTTLE_ERRORS as exc:
            raise InstagramRateLimited(
                "Instagram is temporarily rate limiting logins. Please wait and try again later."
            ) from exc
        except ClientError as exc:
            logger.warning("Instagram login failed (%s)", type(exc).__name__)
            raise InstagramLoginError(
                "Instagram rejected the login attempt. Please verify the credentials and try again."
            ) from exc
        except Exception as exc:
            logger.warning("Instagram login failed (%s)", type(exc).__name__)
            raise InstagramLoginError(
                "Instagram login failed due to a connection or provider error."
            ) from exc

        if not logged_in:
            raise InstagramLoginError(
                "Instagram did not accept the login attempt. Please try again."
            )

        self._auth_method = 'password'
        self._username = self._resolve_authenticated_username()
        self._authenticated = True
        settings = self.client.get_settings()
        self._persist_session(settings)
        return settings

    def retry_login(self) -> Dict[str, Any]:
        if self._auth_method == 'sessionid':
            username = self._resolve_authenticated_username()
            self._authenticated = True
            self._username = username
            return self.client.get_settings()
        if self.two_step_verification_context:
            raise self._verification_pending()
        return self.login()

    def request_sms_code(self) -> None:
        context = getattr(self, 'two_step_verification_context', None)
        if not context:
            raise InstagramLoginError(
                "SMS verification is not available for this login attempt. Please restart the login flow."
            )
        try:
            self.client.bloks_two_step_verification_entrypoint(context, should_fallback_to_sms=True)
            self.client.bloks_two_step_verification_method_picker(context, should_fallback_to_sms=True)
            self.client.bloks_two_step_verification_select_method(
                context, selected_method="sms", should_fallback_to_sms=True
            )
        except _THROTTLE_ERRORS as exc:
            raise InstagramRateLimited(
                "Instagram is temporarily rate limiting SMS requests. Please wait and try again later."
            ) from exc
        except ClientError as exc:
            logger.warning("Instagram SMS request failed (%s)", type(exc).__name__)
            raise InstagramLoginError("Instagram could not send an SMS code for this login attempt.") from exc

    def login_by_sessionid(self, sessionid: str) -> Dict[str, Any]:
        clean_session = str(sessionid or '').strip()
        if not clean_session:
            raise InstagramLoginError("Session ID cannot be empty")

        self._auth_method = 'sessionid'
        try:
            logged_in = self.client.login_by_sessionid(clean_session)
        except _THROTTLE_ERRORS as exc:
            raise InstagramRateLimited(
                "Instagram is temporarily rate limiting logins. Please wait and try again later."
            ) from exc
        except ChallengeRequired as exc:
            raise InstagramManualVerificationRequired(
                "Instagram requires manual verification. Confirm this login in the official "
                "Instagram app or website on a trusted device, then retry."
            ) from exc
        except Exception as exc:
            logger.warning("Instagram session ID login failed (%s)", type(exc).__name__)
            raise InstagramSessionExpired(
                "Instagram rejected the session cookie. It may be invalid or expired."
            ) from exc

        if not logged_in:
            raise InstagramSessionExpired(
                "Instagram rejected the session cookie. It may be invalid or expired."
            )

        self._username = self._resolve_authenticated_username()
        self._authenticated = True
        settings = self.client.get_settings()
        self._persist_session(settings)
        return settings

    def _reuse_saved_session(self) -> Dict[str, Any]:
        try:
            username = self._resolve_authenticated_username()
        except (InstagramRateLimited, InstagramManualVerificationRequired):
            raise
        except InstagramLoginError as exc:
            raise InstagramSessionExpired(
                "The saved Instagram session has expired. Please reconnect the account."
            ) from exc
        self._authenticated = True
        self._auth_method = 'sessionid'
        self._username = username
        settings = self.client.get_settings()
        self._persist_session(settings)
        return settings

    def _persist_session(self, settings: Optional[Dict[str, Any]] = None) -> None:
        """Save the current instagrapi session settings encrypted at rest."""
        try:
            from ...core.session_store import save_session
            save_session(self.account_id or 'default', settings or self.client.get_settings())
        except Exception as exc:
            logger.debug(f"Could not persist Instagram session for '{self.account_id}': {exc}")

    async def authenticate(self) -> Dict[str, Any]:
        """Authenticate with Instagram reusing saved session settings when possible."""
        session_settings = self.config.get('session_settings')

        if self.config.get('password'):
            return await asyncio.to_thread(self.login)

        if session_settings:
            return await asyncio.to_thread(self._reuse_saved_session)

        legacy_sessionid = str(self.config.get('sessionid') or '').strip()
        if legacy_sessionid:
            return await asyncio.to_thread(self.login_by_sessionid, legacy_sessionid)

        raise InstagramSessionExpired(
            "No usable Instagram credentials are stored. Please reconnect the account."
        )

    async def validate_credentials(self) -> bool:
        """Validate that the current credentials are valid"""
        try:
            if not self._authenticated:
                await self.authenticate()
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_story_mention_username(value) -> str:
        candidate = str(value or '').strip().lstrip('@').strip()
        if not re.fullmatch(r'[A-Za-z0-9._]{1,30}', candidate):
            raise PlatformError('Invalid Instagram mention username in story interaction')
        return candidate

    @staticmethod
    def _normalize_story_hashtag_name(value) -> str:
        candidate = str(value or '').strip().lstrip('#').strip()
        if not (1 <= len(candidate) <= 100) or not all(ch.isalnum() or ch == '_' for ch in candidate):
            raise PlatformError('Invalid Instagram hashtag in story interaction')
        return candidate

    @staticmethod
    def _story_geometry(descriptor: Dict[str, Any]) -> Dict[str, float]:
        def _num(key: str, default: float) -> float:
            value = descriptor.get(key)
            if value is None:
                value = default
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise PlatformError('Invalid Instagram story interaction geometry') from exc
        return {
            'x': max(0.0, min(_num('x', 0.0), 1.0)),
            'y': max(0.0, min(_num('y', 0.0), 1.0)),
            'width': max(0.01, min(_num('width', 0.1), 1.0)),
            'height': max(0.01, min(_num('height', 0.05), 1.0)),
            'rotation': _num('rotation', 0.0),
        }

    def _resolve_story_mention_user(self, username: str) -> UserShort:
        cache_key = username.lower()
        if cache_key not in self._story_user_cache:
            try:
                resolved = self.client.user_info_by_username(username)
            except PlatformError:
                raise
            except _THROTTLE_ERRORS as exc:
                raise InstagramRateLimited(
                    "Instagram is temporarily rate limiting logins. Please wait and try again later."
                ) from exc
            except Exception as exc:
                raise PlatformError('Instagram could not resolve the story mention target') from exc
            self._story_user_cache[cache_key] = UserShort(
                pk=str(resolved.pk),
                username=str(getattr(resolved, 'username', '') or username),
                full_name=str(getattr(resolved, 'full_name', '') or ''),
                profile_pic_url=getattr(resolved, 'profile_pic_url', None),
                is_private=getattr(resolved, 'is_private', None),
                is_verified=getattr(resolved, 'is_verified', None),
            )
        return self._story_user_cache[cache_key]

    def _resolve_story_hashtag(self, name: str) -> Hashtag:
        cache_key = name.casefold()
        if cache_key not in self._story_hashtag_cache:
            try:
                resolved = self.client.hashtag_info(name)
            except PlatformError:
                raise
            except _THROTTLE_ERRORS as exc:
                raise InstagramRateLimited(
                    "Instagram is temporarily rate limiting logins. Please wait and try again later."
                ) from exc
            except Exception as exc:
                raise PlatformError('Instagram could not resolve the story hashtag target') from exc
            if isinstance(resolved, Hashtag):
                self._story_hashtag_cache[cache_key] = resolved
            else:
                self._story_hashtag_cache[cache_key] = Hashtag(
                    id=str(resolved.id),
                    name=str(resolved.name or name),
                )
        return self._story_hashtag_cache[cache_key]

    def _build_story_mentions(self, descriptors) -> List[StoryMention]:
        mentions = []
        for descriptor in descriptors or []:
            username = self._normalize_story_mention_username(
                descriptor.get('username') if isinstance(descriptor, dict) else None
            )
            geometry = self._story_geometry(descriptor)
            user = self._resolve_story_mention_user(username)
            mentions.append(StoryMention(user=user, **geometry))
        return mentions

    def _build_story_hashtags(self, descriptors) -> List[StoryHashtag]:
        hashtags = []
        for descriptor in descriptors or []:
            name = self._normalize_story_hashtag_name(
                descriptor.get('name') if isinstance(descriptor, dict) else None
            )
            geometry = self._story_geometry(descriptor)
            hashtag = self._resolve_story_hashtag(name)
            hashtags.append(StoryHashtag(hashtag=hashtag, **geometry))
        return hashtags

    async def post_content(self, content: Dict[str, Any]) -> str:
        """Post content to Instagram Stories (supports image and video)
        
        Args:
            content: Dictionary containing content details
                    Required keys: (image_path or video_path)
                    Optional keys: url, caption, sticker_x, sticker_y,
                    sticker_width, sticker_height, story_mentions, story_hashtags
        
        Returns:
            str: Story media ID
        """
        if not self._authenticated:
            await self.authenticate()
            
        try:
            media_path = content.get('video_path') or content.get('image_path')
            if not media_path:
                raise PlatformError("No media path (image_path or video_path) provided for Instagram Story")

            _, ext = os.path.splitext(media_path)
            is_video = ext.lower() in ('.mp4', '.mov', '.m4v', '.webm')
            suffix = ext.lower() if ext else ('.mp4' if is_video else '.jpg')

            # Create temporary file for the media
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_path = temp_file.name
                
            # Copy media to temp file (some files can't be uploaded directly)
            with open(media_path, 'rb') as src, open(temp_path, 'wb') as dst:
                dst.write(src.read())
            
            link_url = str(content.get('url') or '').strip()
            links = []
            if link_url:
                links.append(StoryLink(
                    webUri=link_url,
                    x=float(content.get('sticker_x', 0.5)),
                    y=float(content.get('sticker_y', 0.7)),
                    z=1,
                    width=float(content.get('sticker_width', 0.5)),
                    height=float(content.get('sticker_height', 0.1)),
                    rotation=0.0,
                ))
            
            # Upload the story on non-blocking worker thread
            def upload_sync():
                mentions = self._build_story_mentions(content.get('story_mentions'))
                hashtags = self._build_story_hashtags(content.get('story_hashtags'))
                if is_video:
                    return self.client.video_upload_to_story(
                        temp_path,
                        caption=content.get('caption', ''),
                        links=links,
                        mentions=mentions,
                        hashtags=hashtags,
                    )
                else:
                    return self.client.photo_upload_to_story(
                        temp_path,
                        caption=content.get('caption', ''),
                        links=links,
                        mentions=mentions,
                        hashtags=hashtags,
                    )

            story = await asyncio.to_thread(upload_sync)
            
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            
            media_type = "video" if is_video else "photo"
            logger.info(f"Successfully posted {media_type} to Instagram Stories for account '{self.account_id}'")
            return str(story.pk)
            
        except (PlatformError, AuthenticationError):
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        except Exception as e:
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.unlink(temp_path)
            raise PlatformError(f"Failed to post to Instagram Story for account '{self.account_id}': {str(e)}")

    async def delete_story(self, media_id: str) -> None:
        """Delete a story by its media ID"""
        if not self._authenticated:
            await self.authenticate()
            
        try:
            story_pk = int(media_id)
            await asyncio.to_thread(self.client.story_delete, story_pk)
            logger.info(f"Successfully deleted Instagram Story {media_id} for account '{self.account_id}'")
        except Exception as e:
            raise PlatformError(f"Failed to delete Instagram Story {media_id} for account '{self.account_id}': {str(e)}")
