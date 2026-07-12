import os
from dotenv import load_dotenv
from typing import Dict, Any, List
from ..core.exceptions import ConfigError

class Config:
    """Configuration management for StreamShare (Simplified CLI version)"""
    _instance = None
    _initialized = False

    REQUIRED_INSTAGRAM_FIELDS = ['username', 'password']
    REQUIRED_TWITTER_FIELDS = ['api_key', 'api_secret', 'access_token', 'access_token_secret']

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _validate_account_config(self, platform: str, account_id: str, config: Dict[str, str], required_fields: List[str]) -> None:
        missing_fields = [field for field in required_fields if not config.get(field)]
        if missing_fields:
            raise ConfigError(
                f"Missing required fields for {platform} account '{account_id}': {', '.join(missing_fields)}\n"
                f"Please ensure these fields are set in your .env file."
            )

    def __init__(self):
        if not self._initialized:
            load_dotenv()
            
            # Load the base configuration
            self._config = {
                'default_source': {
                    'platform': os.getenv('DEFAULT_SOURCE_PLATFORM', 'twitch'),
                    'identifier': os.getenv('DEFAULT_SOURCE_IDENTIFIER'),
                },
                'twitch': {
                    'client_id': os.getenv('TWITCH_CLIENT_ID'),
                    'client_secret': os.getenv('TWITCH_CLIENT_SECRET'),
                    'username': os.getenv('TWITCH_USERNAME'),
                    'thumbnail_resolution': os.getenv('TWITCH_THUMBNAIL_RESOLUTION', '1920x1080'),
                    'boxart_resolution': os.getenv('TWITCH_BOXART_RESOLUTION', '600x800'),
                    'ultrawide': False,
                },
                'media': {
                    'font_path': None,  # No fonts or assets in public version
                    'logo_path': None,
                }
            }

            # Load accounts
            instagram_accounts = {}
            if os.getenv('INSTAGRAM_USERNAME'):
                instagram_accounts['default'] = {
                    'username': os.getenv('INSTAGRAM_USERNAME'),
                    'password': os.getenv('INSTAGRAM_PASSWORD'),
                    'totp_secret': os.getenv('INSTAGRAM_TOTP_SECRET'),
                    'enabled': True
                }

            twitter_accounts = {}
            if os.getenv('TWITTER_API_KEY'):
                twitter_accounts['default'] = {
                    'api_key': os.getenv('TWITTER_API_KEY'),
                    'api_secret': os.getenv('TWITTER_API_SECRET'),
                    'access_token': os.getenv('TWITTER_ACCESS_TOKEN'),
                    'access_token_secret': os.getenv('TWITTER_ACCESS_TOKEN_SECRET'),
                    'enabled': True
                }

            discord_accounts = {}
            if os.getenv('DISCORD_WEBHOOK_URL'):
                discord_accounts['default'] = {
                    'webhook_url': os.getenv('DISCORD_WEBHOOK_URL'),
                    'enabled': True
                }

            # Validate configurations if they are provided
            for account_id, config in instagram_accounts.items():
                if config.get('enabled', False):
                    self._validate_account_config('instagram', account_id, config, self.REQUIRED_INSTAGRAM_FIELDS)

            for account_id, config in twitter_accounts.items():
                if config.get('enabled', False):
                    self._validate_account_config('twitter', account_id, config, self.REQUIRED_TWITTER_FIELDS)

            self._config['instagram_accounts'] = instagram_accounts
            self._config['twitter_accounts'] = twitter_accounts
            self._config['discord_accounts'] = discord_accounts

            self._initialized = True

    def get(self, section: str, default: Any = None) -> Dict[str, Any]:
        """Get a configuration section"""
        if section == 'instagram' and 'default' in self._config.get('instagram_accounts', {}):
            return self._config['instagram_accounts']['default']
        elif section == 'twitter' and 'default' in self._config.get('twitter_accounts', {}):
            return self._config['twitter_accounts']['default']
        elif section == 'discord' and 'default' in self._config.get('discord_accounts', {}):
            return self._config['discord_accounts']['default']
        return self._config.get(section, default or {})

    def update(self, section: str, key: str, value: Any) -> None:
        """Update a configuration value"""
        if section in self._config:
            self._config[section][key] = value
