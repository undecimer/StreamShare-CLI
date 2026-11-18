from typing import Dict, Any, Optional
import asyncio
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
            # Try OAuth 1.0a verification first
            auth = tweepy.OAuth1UserHandler(
                consumer_key=self.config['api_key'],
                consumer_secret=self.config['api_secret'],
                callback="oob"
            )
            
            # Just try to get the authorization URL
            auth.get_authorization_url()
            return True
            
        except tweepy.TweepyException as e:
            print(f"\nError: Twitter API credentials are invalid for account '{self.account_id}'. Please verify in the Developer Portal:")
            print("1. You are using the correct API Key and Secret")
            print("2. Your app is in the correct environment (Production/Development)")
            print("3. Your app has OAuth 1.0a enabled")
            print("4. Your app has User authentication settings enabled")
            print("5. Your app has Read and Write permissions")
            print("\nSpecific steps:")
            print("1. Go to https://developer.twitter.com/en/portal/projects")
            print("2. Select your project and app")
            print("3. Go to 'Settings' > 'User authentication settings'")
            print("4. Enable 'OAuth 1.0a'")
            print("5. Set App permissions to 'Read and write'")
            print("6. Set Type of App to 'Native App' or 'Web App'")
            print("7. Save changes and regenerate your tokens")
            print(f"\nError details: {str(e)}")
            return False

    def _generate_access_tokens(self) -> None:
        """Generate Twitter access tokens via OAuth 1.0a flow"""
        try:
            # First verify API credentials
            if not self._verify_api_credentials():
                exit(1)
            
            print(f"\nGenerating Twitter access tokens for account '{self.account_id}'...")
            print("1. Make sure you have configured these settings in Twitter Developer Portal:")
            print("   - OAuth 1.0a is enabled")
            print("   - App permissions include Read and Write")
            print("   - Callback URL is set (can be any URL)\n")
            
            auth = tweepy.OAuth1UserHandler(
                self.config['api_key'],
                self.config['api_secret']
            )
            
            try:
                auth_url = auth.get_authorization_url()
                print(f"2. Visit this URL to authorize the app:\n{auth_url}\n")
                verifier = input("3. Enter the PIN code: ")
                
                # Get access token and secret
                auth.get_access_token(verifier)
                access_token = auth.access_token
                access_token_secret = auth.access_token_secret
                
                print(f"\nAdd these lines to your .env file for account '{self.account_id}':")
                print("----------------------------------------")
                print(f"TWITTER_{self.account_id.upper()}_ACCESS_TOKEN={access_token}")
                print(f"TWITTER_{self.account_id.upper()}_ACCESS_TOKEN_SECRET={access_token_secret}")
                print("----------------------------------------")
                print("\nAfter adding these to your .env file, restart the application.")
                
            except tweepy.TweepyException as e:
                if "401 Unauthorized" in str(e):
                    print("\nError: Could not get authorization URL. Please verify:")
                    print("1. Your API key and secret are correct")
                    print("2. OAuth 1.0a is enabled in Twitter Developer Portal")
                    print("3. App permissions include Read and Write")
                    print("4. Callback URL is set in Developer Portal")
                raise
                
        except Exception as e:
            print(f"\nFailed to generate tokens: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    print(f"Twitter API response: {error_data}")
                except:
                    print(f"Twitter API status code: {e.response.status_code}")
            raise Exception(f"Failed to generate tokens: {str(e)}")

    async def authenticate(self) -> None:
        """Authenticate with Twitter using OAuth 1.0a PIN-based flow"""
        try:
            logger.info(f"Authenticating Twitter account '{self.account_id}'...")
            
            # Check for required credentials
            required_keys = ['api_key', 'api_secret', 'access_token', 'access_token_secret']
            if not all(key in self.config for key in required_keys):
                logger.error(f"Missing Twitter API credentials for account '{self.account_id}'. Required: {', '.join(required_keys)}")
                raise Exception(f"Missing Twitter API credentials for account '{self.account_id}'")

            # First verify the API credentials
            if not self._verify_api_credentials():
                raise Exception("Twitter authentication failed")

            # Initialize API v1.1 client (needed for media upload)
            auth = tweepy.OAuth1UserHandler(
                consumer_key=self.config['api_key'],
                consumer_secret=self.config['api_secret'],
                access_token=self.config['access_token'],
                access_token_secret=self.config['access_token_secret']
            )
            self.api = tweepy.API(auth, wait_on_rate_limit=False)  # Disable auto-waiting
            
            # Initialize API v2 Client
            self.client = tweepy.Client(
                consumer_key=self.config['api_key'],
                consumer_secret=self.config['api_secret'],
                access_token=self.config['access_token'],
                access_token_secret=self.config['access_token_secret'],
                wait_on_rate_limit=False  # Disable auto-waiting
            )
            
            # Test the credentials
            try:
                me = await asyncio.to_thread(self.client.get_me)
                if me and me.data:
                    logger.info(f"Successfully authenticated Twitter account '{self.account_id}'")
                    self._authenticated = True
                else:
                    raise tweepy.TweepyException("Could not verify credentials")
            except tweepy.TweepyException as e:
                print(f"\nError: Could not authenticate account '{self.account_id}'. Please verify:")
                print("1. Your API key and secret are correct")
                print("2. Your access token and secret are correct")
                print("3. OAuth 1.0a is enabled in Twitter Developer Portal")
                print("4. App permissions include Read and Write")
                print(f"\nError details: {str(e)}")
                raise
            
        except Exception as e:
            error_msg = f"Failed to authenticate Twitter account '{self.account_id}': {str(e)}"
            logger.error(error_msg)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"Twitter API response: {error_data}")
                except:
                    logger.error(f"Twitter API status code: {e.response.status_code}")
            raise AuthenticationError(error_msg)

    async def validate_credentials(self) -> bool:
        """Check if current credentials are valid"""
        try:
            if not self.client:
                await self.authenticate()
            me = await asyncio.to_thread(self.client.get_me)
            if me and me.data:
                logger.info(f"Twitter credentials validated successfully for account '{self.account_id}'")
                return True
            else:
                raise tweepy.TweepyException("Could not verify credentials")
        except Exception as e:
            logger.error(f"Failed to validate Twitter credentials for account '{self.account_id}': {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"Twitter API response: {error_data}")
                except:
                    logger.error(f"Twitter API status code: {e.response.status_code}")
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
            str: Tweet ID or 'manual' if rate limited, or JSON string with main and response IDs if use_response is True
        """
        if not self._authenticated:
            await self.authenticate()

        try:
            if not await self.validate_credentials():
                raise AuthenticationError(f"Failed to authenticate Twitter account '{self.account_id}'")

            # Create tweet text first
            use_response = content.get('use_response', False)
            if use_response:
                tweet_text = content.get('reply_main_text') or content.get('text', '')
            else:
                tweet_text = content.get('text', '')

            try:
                # Upload media non-blockingly on worker thread since v1.1 upload is synchronous
                logger.info(f"Uploading media to Twitter for account '{self.account_id}'...")
                media = await asyncio.to_thread(self.api.media_upload, content['image_path'])
                media_id = media.media_id
                
                # Check if we should use response tweet for URL
                url = content.get('url', '')
                
                logger.info(f"Posting tweet with media asynchronously for account '{self.account_id}'...")
                tweet = await asyncio.to_thread(
                    self.client.create_tweet,
                    text=tweet_text,
                    media_ids=[media_id]
                )
                
                if not tweet or not tweet.data:
                    raise Exception("Failed to post tweet")
                    
                main_tweet_id = str(tweet.data['id'])
                logger.info(f"Successfully posted main tweet with ID: {main_tweet_id} for account '{self.account_id}'")
                
                # If using response tweet, post URL as a response
                if use_response and url:
                    try:
                        logger.info(f"Posting response tweet with URL for account '{self.account_id}'...")
                        response_text = content.get('reply_text') or f"➡️ Ver en {url}"
                        response_tweet = await asyncio.to_thread(
                            self.client.create_tweet,
                            text=response_text,
                            in_reply_to_tweet_id=main_tweet_id
                        )
                        
                        if response_tweet and response_tweet.data:
                            response_tweet_id = str(response_tweet.data['id'])
                            logger.info(f"Successfully posted response tweet with ID: {response_tweet_id} for account '{self.account_id}'")
                            # Return both tweet IDs as JSON string
                            return json.dumps({
                                'main_tweet_id': main_tweet_id,
                                'response_tweet_id': response_tweet_id
                            })
                        else:
                            logger.warning("Failed to post response tweet, returning only main tweet ID")
                            return main_tweet_id
                    except Exception as e:
                        logger.error(f"Error posting response tweet: {str(e)}")
                        logger.warning("Continuing with only main tweet")
                        return main_tweet_id
                        
                return main_tweet_id

            except tweepy.TooManyRequests as e:
                # Handle rate limit by saving locally
                # Create tweets directory if it doesn't exist
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
                tweets_dir = os.path.join(project_root, 'pending_tweets', self.account_id)
                os.makedirs(tweets_dir, exist_ok=True)
                logger.info(f"Created pending tweets directory for account '{self.account_id}': {tweets_dir}")

                # Copy image to tweets directory with timestamp and game info
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                game_name = content.get('game', 'unknown_game').replace(' ', '_')
                image_name = f'tweet_image_{game_name}_{timestamp}.jpg'
                image_path = os.path.join(tweets_dir, image_name)
                shutil.copy2(content['image_path'], image_path)
                
                # Save tweet text
                text_file = os.path.join(tweets_dir, f'tweet_text_{game_name}_{timestamp}.txt')
                with open(text_file, 'w') as f:
                    f.write(tweet_text)
                
                logger.info(f"Saved tweet image to: {image_path}")
                logger.info(f"Saved tweet text to: {text_file}")

                # Get reset time from headers
                reset_time = int(e.response.headers.get('x-rate-limit-reset', 0))
                current_time = int(time.time())
                wait_time = max(0, reset_time - current_time)
                
                # Print instructions for manual posting
                print("\n" + "="*50)
                print(f"Twitter rate limit reached for account '{self.account_id}'. Please post this tweet manually:")
                print("="*50)
                print(f"\nTweet text:\n{tweet_text}")
                print(f"\nImage saved to:\n{image_path}")
                print(f"\nRate limit will reset in: {wait_time} seconds")
                print("\n" + "="*50)
                
                return 'manual'

        except Exception as e:
            logger.error(f"Twitter posting failed for account '{self.account_id}': {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"Twitter API response: {error_data}")
                except:
                    logger.error(f"Twitter API status code: {e.response.status_code}")
            raise

    async def delete_tweet(self, tweet_id: str) -> None:
        """Delete a tweet
        
        Args:
            tweet_id: ID of the tweet to delete or JSON string containing main_tweet_id and response_tweet_id
        """
        if tweet_id == 'manual':
            return
            
        if not self._authenticated:
            await self.authenticate()

        # Check if tweet_id is a JSON string containing both main and response tweet IDs
        try:
            tweet_data = json.loads(tweet_id)
            if isinstance(tweet_data, dict) and 'main_tweet_id' in tweet_data and 'response_tweet_id' in tweet_data:
                # Delete response tweet first (child before parent)
                response_tweet_id = tweet_data['response_tweet_id']
                try:
                    logger.info(f"Deleting response tweet {response_tweet_id} for account '{self.account_id}'...")
                    await asyncio.to_thread(self.client.delete_tweet, response_tweet_id)
                    logger.info(f"Successfully deleted response tweet {response_tweet_id} for account '{self.account_id}'")
                except tweepy.errors.NotFound:
                    logger.warning(f"Response tweet {response_tweet_id} already deleted or not found for account '{self.account_id}'")
                except Exception as e:
                    logger.error(f"Failed to delete response tweet {response_tweet_id}: {str(e)}")
                
                # Then delete main tweet
                main_tweet_id = tweet_data['main_tweet_id']
                try:
                    logger.info(f"Deleting main tweet {main_tweet_id} for account '{self.account_id}'...")
                    await asyncio.to_thread(self.client.delete_tweet, main_tweet_id)
                    logger.info(f"Successfully deleted main tweet {main_tweet_id} for account '{self.account_id}'")
                except tweepy.errors.NotFound:
                    logger.warning(f"Main tweet {main_tweet_id} already deleted or not found for account '{self.account_id}'")
                except Exception as e:
                    raise PlatformError(f"Failed to delete main tweet {main_tweet_id} for account '{self.account_id}': {str(e)}")
                return
        except (json.JSONDecodeError, TypeError):
            # Not a JSON string, treat as a regular tweet ID
            pass
            
        # Regular single tweet deletion
        try:
            # Delete tweet using v2 API
            logger.info(f"Deleting tweet {tweet_id} for account '{self.account_id}'...")
            await asyncio.to_thread(self.client.delete_tweet, tweet_id)
            logger.info(f"Successfully deleted tweet {tweet_id} for account '{self.account_id}'")
        except tweepy.errors.NotFound:
            logger.warning(f"Tweet {tweet_id} already deleted or not found for account '{self.account_id}'")
        except Exception as e:
            raise PlatformError(f"Failed to delete tweet {tweet_id} for account '{self.account_id}': {str(e)}")
