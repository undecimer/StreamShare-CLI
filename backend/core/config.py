import os
from dotenv import load_dotenv
from typing import Dict, Any, List
from ..core.exceptions import ConfigError
from .paths import get_backend_root, get_repo_root

def _custom_data_dir() -> str:
    custom = os.environ.get("STREAMSHARE_DATA_DIR")
    if custom:
        return os.path.abspath(custom)
    return None


def get_artwork_dir() -> str:
    """Return the active artwork images directory path."""
    custom = _custom_data_dir()
    if custom:
        custom_dir = os.path.join(custom, "artwork_images")
        os.makedirs(custom_dir, exist_ok=True)
        return custom_dir
    data_dir = os.path.join(os.getcwd(), "data", "artwork_images")
    if os.path.isdir(data_dir):
        return data_dir
    legacy_dir = os.path.join(os.getcwd(), "artwork_images")
    if os.path.isdir(legacy_dir):
        return legacy_dir
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

def get_generated_images_dir() -> str:
    """Return the active generated images directory path."""
    custom = _custom_data_dir()
    if custom:
        custom_dir = os.path.join(custom, "generated_images")
        os.makedirs(custom_dir, exist_ok=True)
        return custom_dir
    data_dir = os.path.join(os.getcwd(), "data", "generated_images")
    if os.path.isdir(data_dir):
        return data_dir
    legacy_dir = os.path.join(os.getcwd(), "generated_images")
    if os.path.isdir(legacy_dir):
        return legacy_dir
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_temp_images_dir() -> str:
    custom = _custom_data_dir()
    if custom:
        temp_dir = os.path.join(get_generated_images_dir(), "temp")
    else:
        temp_dir = os.path.join(os.getcwd(), "generated_images", "temp")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def _source_assets_dir() -> str:
    backend_assets = os.path.join(get_backend_root(), "assets")
    if os.path.isdir(backend_assets):
        return backend_assets
    return os.path.join(get_repo_root(), "assets")

class Config:
    """Configuration management for StreamShare"""
    _instance = None
    _initialized = False

    # Define required fields for each platform
    REQUIRED_INSTAGRAM_FIELDS = ['username', 'password']
    REQUIRED_TWITTER_FIELDS = ['api_key', 'api_secret', 'access_token', 'access_token_secret']

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _validate_account_config(self, platform: str, account_id: str, config: Dict[str, str], required_fields: List[str]) -> None:
        """Validate that all required fields are present for an account
        
        Args:
            platform: Platform name ('instagram' or 'twitter')
            account_id: Account identifier
            config: Account configuration dictionary
            required_fields: List of required field names
        
        Raises:
            ConfigError: If any required fields are missing
        """
        missing_fields = [field for field in required_fields if field not in config]
        if missing_fields:
            raise ConfigError(
                f"Missing required fields for {platform} account '{account_id}': {', '.join(missing_fields)}\n"
                f"Please ensure these fields are set in your .env file as:\n"
                + "\n".join([f"{platform.upper()}_{account_id.upper()}_{field.upper()}=value" for field in missing_fields])
            )

    def __init__(self):
        if not self._initialized:
            load_dotenv()
            
            # Helper to group environment variables by prefix and identifier
            def get_account_configs(prefix: str) -> Dict[str, Dict[str, str]]:
                accounts = {}
                # Get all env vars starting with the prefix
                for key, value in os.environ.items():
                    if key.startswith(prefix):
                        # Extract the identifier and property name
                        # e.g., INSTAGRAM_MYACCOUNT_USERNAME -> ('myaccount', 'username')
                        parts = key[len(prefix):].lower().split('_', 1)
                        if len(parts) == 2:
                            account_id, prop = parts
                            if account_id not in accounts:
                                accounts[account_id] = {}  # Don't set enabled by default
                            # Convert enabled string to boolean
                            if prop == 'enabled':
                                accounts[account_id][prop] = value.lower() == 'true'
                            else:
                                accounts[account_id][prop] = value
                return accounts

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
                    'ultrawide': False,  # Default to False, will be set by CLI flag
                },

                'media': {
                    'font_path': os.path.join(_source_assets_dir(), 'fonts', 'Roboto-Regular.ttf'),
                    'logo_path': os.path.join(_source_assets_dir(), 'images'),
                }
            }

            # Load multi-account configurations
            instagram_accounts = get_account_configs('INSTAGRAM_')
            twitter_accounts = get_account_configs('TWITTER_')
            discord_accounts = get_account_configs('DISCORD_')
            if not instagram_accounts and os.getenv('INSTAGRAM_USERNAME'):
                instagram_accounts['default'] = {
                    'username': os.getenv('INSTAGRAM_USERNAME'),
                    'password': os.getenv('INSTAGRAM_PASSWORD'),
                    'totp_secret': os.getenv('INSTAGRAM_TOTP_SECRET'),
                    'enabled': True
                }

            if not twitter_accounts and os.getenv('TWITTER_API_KEY'):
                twitter_accounts['default'] = {
                    'api_key': os.getenv('TWITTER_API_KEY'),
                    'api_secret': os.getenv('TWITTER_API_SECRET'),
                    'access_token': os.getenv('TWITTER_ACCESS_TOKEN'),
                    'access_token_secret': os.getenv('TWITTER_ACCESS_TOKEN_SECRET'),
                    'enabled': True
                }

            # Validate Instagram accounts
            for account_id, config in instagram_accounts.items():
                # Skip validation for disabled accounts
                if not config.get('enabled', False):
                    continue
                self._validate_account_config('instagram', account_id, config, self.REQUIRED_INSTAGRAM_FIELDS)

            # Validate Twitter accounts
            for account_id, config in twitter_accounts.items():
                # Skip validation for disabled accounts
                if not config.get('enabled', False):
                    continue
                self._validate_account_config('twitter', account_id, config, self.REQUIRED_TWITTER_FIELDS)

            if not discord_accounts and os.getenv('DISCORD_WEBHOOK_URL'):
                discord_accounts['default'] = {
                    'webhook_url': os.getenv('DISCORD_WEBHOOK_URL'),
                    'enabled': True
                }

            # Locally configured env accounts are self-authorized: mark them so
            # the publisher's destination authorization accepts the local
            # authorizer without an ecosystem publish grant.
            for accounts in (instagram_accounts, twitter_accounts, discord_accounts):
                for account in accounts.values():
                    account.setdefault('connection_id', 'local')

            # Add account configurations
            self._config['instagram_accounts'] = instagram_accounts
            self._config['twitter_accounts'] = twitter_accounts
            self._config['discord_accounts'] = discord_accounts

            self._initialized = True


    def get(self, section: str, default: Any = None) -> Dict[str, Any]:
        """Get a configuration section
        
        Args:
            section: Configuration section name
            default: Default value if section not found
            
        Returns:
            Configuration section or default value
        """
        # For backward compatibility, map old instagram/twitter sections to default accounts
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
