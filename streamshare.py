from twitchAPI.twitch import Twitch
from instagrapi import Client
import tweepy

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
twitch = Twitch(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)

# Initialize Instagram client
instagram = Client()
instagram.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

# Initialize Twitter client
twitter = tweepy.Client(
    consumer_key=TWITTER_API_KEY,
    consumer_secret=TWITTER_API_SECRET,
    access_token=TWITTER_ACCESS_TOKEN,
    access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
)

# Fetch livestream details
def fetch_livestream_details(username):
    try:
        user = twitch.get_users(logins=[username])
        if not user.data:
            return None
        user_id = user.data[0].id
        stream = twitch.get_streams(user_id=user_id)
        if not stream.data:
            return None
        game = twitch.get_games(game_ids=[stream.data[0].game_id])
        return {
            'url': f'https://www.twitch.tv/{username}',
            'category': stream.data[0].game_name,
            'title': stream.data[0].title,
            'box_art_url': game.data[0].box_art_url.replace('{width}x{height}', '1080x1920')
        }
    except Exception as e:
        print(f'Error fetching livestream details: {e}')
        return None

# Post to Instagram Stories
def post_to_instagram_story(stream_url, category, title, box_art_url):
    try:
        story = instagram.story_photo(
            photo=box_art_url,
            caption=f'🎥 Live Now: {title}\n\nCategory: {category}\n\nWatch here: {stream_url}'
        )
        print(f'Posted to Instagram Stories: {story.id}')
    except Exception as e:
        print(f'Error posting to Instagram Stories: {e}')

# Post to Twitter
def post_to_tweet(stream_url, category, title, box_art_url):
    try:
        tweet = twitter.create_tweet(
            text=f'🎥 Live Now: {title}\n\nCategory: {category}\n\nWatch here: {stream_url}'
        )
        print(f'Posted to Twitter: {tweet.data["id"]}')
    except Exception as e:
        print(f'Error posting to Twitter: {e}')

# Main function
def main():
    stream_details = fetch_livestream_details('your_twitch_username')
    if stream_details:
        post_to_instagram_story(stream_details['url'], stream_details['category'], stream_details['title'], stream_details['box_art_url'])
        post_to_tweet(stream_details['url'], stream_details['category'], stream_details['title'], stream_details['box_art_url'])

if __name__ == '__main__':
    main()
