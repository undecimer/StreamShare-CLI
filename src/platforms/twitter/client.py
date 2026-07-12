from typing import Dict, Any, Optional
import tweepy
import json
from ..base import BasePlatform
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError, ConfigError
import logging
import time
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

class TwitterClient(BasePlatform):
    def __init__(self, account_id: Optional[str] = None, credentials: Dict[str, Any] = None):
        super().__init__(account_id)
        
        if credentials:
            self.config = credentials
        else:
            config = Config()
            accounts = config.get('twitter_accounts')
            if not accounts:
                raise ConfigError("No Twitter accounts configured")
            
            if self.account_id not in accounts:
                raise ConfigError(f"Twitter account '{self.account_id}' not found")
                
            self.config = accounts[self.account_id]
            
        if not self.config.get('enabled', True):
            raise ConfigError(f"Twitter account '{self.account_id}' is disabled")
            
        self.client: Optional[tweepy.Client] = None
        self.api: Optional[tweepy.API] = None
        self._authenticated = False

    def _verify_api_credentials(self) -> bool:
        """Verify that the API key and secret are valid"""
        try:
            auth = tweepy.OAuth1UserHandler(
                consumer_key=self.config['api_key'],
                consumer_secret=self.config['api_secret'],
                callback="oob"
            )
            auth.get_authorization_url()
            return True
        except tweepy.TweepyException as e:
            logger.error(f"Twitter API credentials verification failed: {e}")
            return False

    def _generate_access_tokens(self) -> None:
        """Generate Twitter access tokens via OAuth 1.0a flow"""
        try:
            if not self._verify_api_credentials():
                logger.error("Invalid Twitter API key/secret. Check developer portal settings.")
                return
            
            print(f"\nGenerating Twitter access tokens for account '{self.account_id}'...")
            auth = tweepy.OAuth1UserHandler(
                self.config['api_key'],
                self.config['api_secret']
            )
            
            try:
                auth_url = auth.get_authorization_url()
                print(f"1. Visit this URL to authorize the app:\n{auth_url}\n")
                verifier = input("2. Enter the PIN code: ")
                
                auth.get_access_token(verifier)
                access_token = auth.access_token
                access_token_secret = auth.access_token_secret
                
                print(f"\nAdd these lines to your .env file:")
                print("----------------------------------------")
                print(f"TWITTER_ACCESS_TOKEN={access_token}")
                print(f"TWITTER_ACCESS_TOKEN_SECRET={access_token_secret}")
                print("----------------------------------------")
                
            except tweepy.TweepyException as e:
                logger.error(f"Error getting authorization URL: {e}")
                raise
                
        except Exception as e:
            logger.error(f"Failed to generate Twitter access tokens: {e}")
            raise

    async def authenticate(self) -> None:
        """Authenticate with Twitter using OAuth 1.0a"""
        try:
            logger.info(f"Authenticating Twitter account '{self.account_id}'...")
            
            required_keys = ['api_key', 'api_secret', 'access_token', 'access_token_secret']
            if not all(key in self.config for key in required_keys):
                raise ConfigError(f"Missing Twitter API credentials for account '{self.account_id}'")

            if not self._verify_api_credentials():
                raise AuthenticationError("Twitter API Key or API Secret is invalid.")

            auth = tweepy.OAuth1UserHandler(
                consumer_key=self.config['api_key'],
                consumer_secret=self.config['api_secret'],
                access_token=self.config['access_token'],
                access_token_secret=self.config['access_token_secret']
            )
            self.api = tweepy.API(auth, wait_on_rate_limit=False)
            
            self.client = tweepy.Client(
                consumer_key=self.config['api_key'],
                consumer_secret=self.config['api_secret'],
                access_token=self.config['access_token'],
                access_token_secret=self.config['access_token_secret'],
                wait_on_rate_limit=False
            )
            
            try:
                me = self.client.get_me()
                if me and me.data:
                    logger.info(f"Successfully authenticated Twitter account '{self.account_id}'")
                    self._authenticated = True
                else:
                    raise tweepy.TweepyException("Could not verify credentials")
            except tweepy.TweepyException as e:
                logger.error(f"Could not authenticate Twitter client: {e}")
                raise
            
        except Exception as e:
            error_msg = f"Failed to authenticate Twitter account '{self.account_id}': {str(e)}"
            logger.error(error_msg)
            raise AuthenticationError(error_msg)

    async def validate_credentials(self) -> bool:
        """Check if current credentials are valid"""
        try:
            if not self.api:
                await self.authenticate()
            me = self.client.get_me()
            return me is not None and me.data is not None
        except Exception:
            return False

    async def post_content(self, content: Dict[str, Any]) -> str:
        """Post content to Twitter
        
        Args:
            content: Dictionary containing:
                    - image_path: Path to image file
                    - text: Tweet text
                    - url: URL to include
                    - title: Stream title
                    - game: Game name
                    - use_response: Whether to post URL in a response tweet
                    
        Returns:
            str: Tweet ID or 'manual' if rate limited
        """
        if not self._authenticated:
            await self.authenticate()

        try:
            if not await self.validate_credentials():
                raise AuthenticationError(f"Failed to authenticate Twitter account '{self.account_id}'")

            use_response = content.get('use_response', False)
            tweet_text = content.get('reply_main_text') if use_response else content.get('text', '')
            if not tweet_text:
                tweet_text = content.get('text', '')

            try:
                # Upload media
                logger.info(f"Uploading media to Twitter...")
                media = self.api.media_upload(content['image_path'])
                media_id = media.media_id
                
                url = content.get('url', '')
                logger.info(f"Posting tweet...")
                tweet = self.client.create_tweet(
                    text=tweet_text,
                    media_ids=[media_id]
                )
                
                if not tweet or not tweet.data:
                    raise PlatformError("Failed to post tweet")
                    
                main_tweet_id = str(tweet.data['id'])
                logger.info(f"Successfully posted tweet with ID: {main_tweet_id}")
                
                if use_response and url:
                    try:
                        logger.info(f"Posting response tweet with URL...")
                        response_text = content.get('reply_text') or f"➡️ Ver en {url}"
                        response_tweet = self.client.create_tweet(
                            text=response_text,
                            in_reply_to_tweet_id=main_tweet_id
                        )
                        if response_tweet and response_tweet.data:
                            response_tweet_id = str(response_tweet.data['id'])
                            logger.info(f"Successfully posted response tweet with ID: {response_tweet_id}")
                            return json.dumps({
                                'main_tweet_id': main_tweet_id,
                                'response_tweet_id': response_tweet_id
                            })
                    except Exception as e:
                        logger.error(f"Error posting response tweet: {str(e)}")
                        
                return main_tweet_id

            except tweepy.TooManyRequests as e:
                # Handle rate limiting locally by saving to pending_tweets
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
                tweets_dir = os.path.join(project_root, 'pending_tweets', self.account_id)
                os.makedirs(tweets_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                game_name = content.get('game', 'unknown_game').replace(' ', '_')
                image_name = f'tweet_image_{game_name}_{timestamp}.jpg'
                image_path = os.path.join(tweets_dir, image_name)
                shutil.copy2(content['image_path'], image_path)
                
                text_file = os.path.join(tweets_dir, f'tweet_text_{game_name}_{timestamp}.txt')
                with open(text_file, 'w') as f:
                    f.write(tweet_text)
                
                logger.warning(f"Twitter rate limit hit. Saved pending tweet to {tweets_dir}")
                
                reset_time = int(e.response.headers.get('x-rate-limit-reset', 0))
                wait_time = max(0, reset_time - int(time.time()))
                
                print("\n" + "="*50)
                print(f"Twitter rate limit reached for account '{self.account_id}'. Please post manually:")
                print("="*50)
                print(f"\nTweet text:\n{tweet_text}")
                print(f"\nImage saved to:\n{image_path}")
                print(f"\nRate limit will reset in: {wait_time} seconds")
                print("="*50 + "\n")
                
                return 'manual'

        except Exception as e:
            logger.error(f"Twitter posting failed: {str(e)}")
            raise

    async def delete_tweet(self, tweet_id: str) -> None:
        """Delete a tweet"""
        if tweet_id == 'manual':
            return
            
        if not self._authenticated:
            await self.authenticate()

        # Handle compound JSON tweet IDs (main + reply)
        try:
            tweet_data = json.loads(tweet_id)
            if isinstance(tweet_data, dict) and 'main_tweet_id' in tweet_data and 'response_tweet_id' in tweet_data:
                # Delete response tweet first
                response_id = tweet_data['response_tweet_id']
                try:
                    logger.info(f"Deleting response tweet {response_id}...")
                    self.client.delete_tweet(response_id)
                except tweepy.errors.NotFound:
                    pass
                
                # Delete main tweet
                main_id = tweet_data['main_tweet_id']
                try:
                    logger.info(f"Deleting main tweet {main_id}...")
                    self.client.delete_tweet(main_id)
                except tweepy.errors.NotFound:
                    pass
                return
        except (json.JSONDecodeError, TypeError):
            pass
            
        try:
            logger.info(f"Deleting tweet {tweet_id}...")
            self.client.delete_tweet(tweet_id)
        except tweepy.errors.NotFound:
            logger.warning(f"Tweet {tweet_id} not found.")
        except Exception as e:
            raise PlatformError(f"Failed to delete tweet {tweet_id}: {str(e)}")
