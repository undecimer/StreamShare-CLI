from twitchAPI.twitch import Twitch
from instagrapi import Client
import tweepy
from PIL import Image
import requests
from io import BytesIO
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('streamshare.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Twitch API credentials
TWITCH_CLIENT_ID = 'your_twitch_client_id'
TWITCH_CLIENT_SECRET = 'your_twitch_client_secret'

# Instagram credentials
INSTAGRAM_USERNAME = 'your_instagram_username'
INSTAGRAM_PASSWORD = 'your_instagram_password'

# Twitter API credentials
TWITTER_API_KEY = 'your_twitter_api_key'
TWITTER_API_SECRET = 'your_twitter_api_secret'
TWITTER_ACCESS_TOKEN = 'your_twitter_access_token'
TWITTER_ACCESS_TOKEN_SECRET = 'your_twitter_access_token_secret'

# Initialize Twitch API
try:
    twitch = Twitch(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
    logger.info('Twitch API initialized successfully')
except Exception as e:
    logger.error(f'Failed to initialize Twitch API: {e}')
    raise

# Initialize Instagram client
try:
    instagram = Client()
    instagram.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    logger.info('Instagram client initialized successfully')
except Exception as e:
    logger.error(f'Failed to initialize Instagram client: {e}')
    raise

# Initialize Twitter client
try:
    twitter = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
    )
    logger.info('Twitter client initialized successfully')
except Exception as e:
    logger.error(f'Failed to initialize Twitter client: {e}')
    raise

# Fetch livestream details
def fetch_livestream_details(username):
    try:
        user = twitch.get_users(logins=[username])
        if not user.data:
            logger.warning(f'No user found with username: {username}')
            return None
        user_id = user.data[0].id
        stream = twitch.get_streams(user_id=user_id)
        if not stream.data:
            logger.warning(f'No active stream found for user: {username}')
            return None
        game = twitch.get_games(game_ids=[stream.data[0].game_id])
        logger.info(f'Successfully fetched livestream details for {username}')
        return {
            'url': f'https://www.twitch.tv/{username}',
            'category': stream.data[0].game_name,
            'title': stream.data[0].title,
            'box_art_url': game.data[0].box_art_url.replace('{width}x{height}', '1080x1920'),
            'original_box_art_url': game.data[0].box_art_url.replace('{width}x{height}', '1920x1080')
        }
    except Exception as e:
        logger.error(f'Error fetching livestream details: {e}')
        return None

# Crop image based on destination
def resize_image(image_url, destination):
    try:
        response = requests.get(image_url)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
        width, height = image.size
        if destination == 'instagram':
            # Crop to 1080x1920 (portrait)
            new_width = min(width, int(height * (1080 / 1920)))
            new_height = min(height, int(width * (1920 / 1080)))
            left = (width - new_width) / 2
            top = (height - new_height) / 2
            right = (width + new_width) / 2
            bottom = (height + new_height) / 2
            cropped_image = image.crop((left, top, right, bottom))
            return cropped_image.resize((1080, 1920))
        elif destination == 'twitter':
            # Crop to 1280x720 (landscape)
            new_width = min(width, int(height * (1280 / 720)))
            new_height = min(height, int(width * (720 / 1280)))
            left = (width - new_width) / 2
            top = (height - new_height) / 2
            right = (width + new_width) / 2
            bottom = (height + new_height) / 2
            cropped_image = image.crop((left, top, right, bottom))
            return cropped_image.resize((1280, 720))
        return image
    except Exception as e:
        logger.error(f'Error resizing image: {e}')
        return None

# Post to Instagram Stories
def post_to_instagram_story(stream_url, category, title, box_art_url):
    try:
        resized_image = resize_image(box_art_url, 'instagram')
        if resized_image:
            story = instagram.story_photo(
                photo=resized_image,
                caption=f'🎥 Live Now: {title}\n\nCategory: {category}\n\nWatch here: {stream_url}'
            )
            logger.info(f'Posted to Instagram Stories: {story.id}')
    except Exception as e:
        logger.error(f'Error posting to Instagram Stories: {e}')

# Post to Twitter
def post_to_twitter(stream_url, category, title, box_art_url):
    try:
        resized_image = resize_image(box_art_url, 'twitter')
        if resized_image:
            media = twitter.media_upload('image.jpg', resized_image)
            tweet = twitter.create_tweet(
                text=f'🎥 Live Now: {title}\n\nCategory: {category}\n\nWatch here: {stream_url}',
                media_ids=[media.media_id]
            )
            logger.info(f'Posted to Twitter: {tweet.data["id"]}')
    except Exception as e:
        logger.error(f'Error posting to Twitter: {e}')

# Main function
def main():
    stream_details = fetch_livestream_details('your_twitch_username')
    if stream_details:
        post_to_instagram_story(stream_details['url'], stream_details['category'], stream_details['title'], stream_details['original_box_art_url'])
        post_to_twitter(stream_details['url'], stream_details['category'], stream_details['title'], stream_details['original_box_art_url'])

if __name__ == '__main__':
    main()
