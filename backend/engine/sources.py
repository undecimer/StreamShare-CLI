import inspect
from typing import Dict, Any, Optional
from ..core.config import Config
from ..core.exceptions import PlatformError
from ..platforms.sources.base import StreamSource
from ..platforms.sources.twitch import TwitchSource
from ..platforms.sources.tiktok import TikTokSource
from ..platforms.sources.youtube import YoutubeSource
from ..platforms.sources.kick import KickSource


class AuthorizedStreamSource:
    """Grant-guarded wrapper around a concrete ``StreamSource``.

    Installed only when a web worker supplies ``source_authorizer`` — an
    async callable returning the verified authorization dict (the OneSite
    ``/authorization`` proof) or raising when the grant cannot authorize the
    call. Every network-facing method first obtains a fresh proof; results
    carrying a provider canonical id (Twitch ``user_id``, YouTube
    ``channel_id``) are compared against ``proof['platformUserId']`` before
    the result may reach publishing or state dispatch. TikTok app-scoped
    identifiers are never assumed equal: a returned identifier that cannot be
    proven identical to the authorized resource is an explicit
    ``authorization_unsupported`` failure. Pure helpers (``parse_identifier``,
    ``get_platform_name``) and unknown attributes pass straight through.
    """

    _GUARDED_METHODS = frozenset({
        'authenticate', 'validate_credentials',
        'get_stream_info', 'get_stream_info_with_options',
        'get_current_stream_info', 'is_stream_live',
        'get_latest_vod', 'get_game_name', 'get_schedule',
        'start_eventsub', 'subscribe_channel',
    })

    def __init__(self, inner: Any, source_authorizer: Any):
        object.__setattr__(self, '_inner', inner)
        object.__setattr__(self, '_source_authorizer', source_authorizer)

    async def _authorize(self) -> Dict[str, Any]:
        try:
            proof = await self._source_authorizer()
        except PlatformError:
            raise
        except Exception as exc:
            raise PlatformError(f"authorization_unavailable: {exc}") from exc
        if not isinstance(proof, dict) or proof.get('allowed') is not True:
            raise PlatformError("authorization_denied: source authorizer returned no verified grant")
        return proof

    def _inner_platform(self) -> str:
        name_fn = getattr(self._inner, 'get_platform_name', None)
        try:
            return str(name_fn() or '').lower() if callable(name_fn) else ''
        except Exception:
            return ''

    def _validate_result(self, result: Any, proof: Dict[str, Any], method: str) -> Any:
        if method not in {'get_stream_info', 'get_stream_info_with_options', 'get_current_stream_info'} or result is None:
            return result
        if not isinstance(result, dict):
            raise PlatformError("authorization_unsupported: source returned no verifiable stream metadata")
        expected = str(proof.get('platformUserId') or proof.get('platform_user_id') or '')
        if not expected:
            raise PlatformError("authorization_denied: proof carries no canonical platform user id")
        platform = str(result.get('platform') or '').lower() or self._inner_platform()
        returned = None
        if platform == 'twitch':
            returned = result.get('user_id')
        elif platform == 'kick':
            returned = result.get('user_id') or result.get('channel_id')
        elif platform == 'youtube':
            returned = result.get('channel_id')
        elif platform == 'tiktok':
            # App-scoped grants bind the OAuth open_id, which stream results
            # never expose; the provider-observable identity is the verified
            # handle. Prefer a provider-asserted unique_id; otherwise the
            # result's request-echoed identifier must match the grant's
            # verified platformUsername (proof binds the Primary link).
            expected_handle = str(
                proof.get('platformUsername') or proof.get('platform_username') or ''
            ).lstrip('@').lower()
            if not expected_handle:
                raise PlatformError(
                    "authorization_denied: proof carries no verified TikTok handle"
                )
            returned = result.get('unique_id') or result.get('identifier')
            if returned is None or str(returned).lstrip('@').lower() != expected_handle:
                raise PlatformError(
                    "authorization_mismatch: returned tiktok resource "
                    "does not match the authorized grant"
                )
            return result
        if returned is None:
            raise PlatformError("authorization_unsupported: source returned no canonical platform identity")
        if str(returned) != expected:
            raise PlatformError(
                f"authorization_mismatch: returned {platform} resource "
                f"does not match the authorized grant"
            )
        return result

    def __getattr__(self, name: str):
        inner = object.__getattribute__(self, '_inner')
        attr = getattr(inner, name)
        if name not in self._GUARDED_METHODS or not callable(attr):
            return attr

        async def guarded(*args, **kwargs):
            proof = await self._authorize()
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            validated = self._validate_result(result, proof, name)
            # Only a provider call that was authorized AND returned a
            # proof-validated result advances the delegated-work heartbeat —
            # denied or failed checks must let the watcher go stale so the
            # orchestrator resumes standalone polling.
            mark_checked = getattr(self._source_authorizer, 'mark_checked', None)
            if callable(mark_checked):
                mark_checked()
            return validated
        return guarded


def get_source(
    config: Optional[Config] = None,
    platform: Optional[str] = None,
    user_settings: Optional[Dict[str, Any]] = None,
    default_platform: str = 'twitch',
    source_authorizer: Optional[Any] = None,
) -> StreamSource:
    """Get the appropriate stream source."""
    cfg = config or Config()
    if not platform:
        default_source = cfg.get('default_source') or {}
        platform = default_source.get('platform', default_platform)

    plat_lower = str(platform).lower()
    if plat_lower == 'twitch':
        source = TwitchSource(cfg, user_settings)
    elif plat_lower == 'tiktok':
        source = TikTokSource(cfg, user_settings)
    elif plat_lower == 'youtube':
        source = YoutubeSource(cfg, user_settings)
    elif plat_lower == 'kick':
        source = KickSource(cfg, user_settings)
    else:
        raise ValueError(f"Unsupported platform: {platform}")
    if source_authorizer is not None:
        source = AuthorizedStreamSource(source, source_authorizer)
    return source
