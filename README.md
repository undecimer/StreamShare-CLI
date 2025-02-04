# StreamShare

StreamShare is a Python application that shares Twitch livestream links to Instagram Stories and Twitter. It fetches livestream details (URL, category, and title) using the Twitch API and posts them to Instagram and Twitter.

## Features
- Fetch Twitch livestream details (URL, category, and title).
- Fetch the category image in its original resolution.
- Crop the image to fit Instagram Stories (1080x1920) or Twitter (1280x720) while preserving the aspect ratio.
- Post livestream links to Instagram Stories.
- Post livestream links as tweets on Twitter.

## Prerequisites
- Python 3.12+
- Twitch API credentials
- Instagram credentials
- Twitter API credentials

## Setup
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/StreamShare.git
   cd StreamShare
   ```
2. Create a virtual environment:
   ```bash
   python3 -m venv venv
   ```
3. Activate the virtual environment:
   ```bash
   source venv/bin/activate
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Replace placeholder credentials in `streamshare.py` with your actual API keys and credentials.

## Usage
Run the script:
```bash
python streamshare.py
```

## Environment Variables
To avoid hardcoding credentials, you can use environment variables. Add the following to a `.env` file:
```plaintext
TWITCH_CLIENT_ID=your_twitch_client_id
TWITCH_CLIENT_SECRET=your_twitch_client_secret
INSTAGRAM_USERNAME=your_instagram_username
INSTAGRAM_PASSWORD=your_instagram_password
TWITTER_API_KEY=your_twitter_api_key
TWITTER_API_SECRET=your_twitter_api_secret
TWITTER_ACCESS_TOKEN=your_twitter_access_token
TWITTER_ACCESS_TOKEN_SECRET=your_twitter_access_token_secret
```

## Contributing
Contributions are welcome! Please open an issue or submit a pull request.

## License
MIT
