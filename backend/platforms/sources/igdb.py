import aiohttp
import logging
import asyncio
import os
import requests
from typing import Dict, Any, Optional, List, Union
from io import BytesIO
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError, ConfigError

logger = logging.getLogger(__name__)

class IGDBClient:
    def __init__(self, client_id: Union[str, Dict[str, Any], Config] = None, client_secret: str = None) -> None:
        """Initialize IGDB client using Twitch credentials
        
        Args:
            client_id: Twitch Client ID, or config dict, or Config object
            client_secret: Twitch Client Secret
        """
        if isinstance(client_id, dict):
            self.client_id = client_id.get('client_id') or client_id.get('twitch_client_id')
            self.client_secret = client_id.get('client_secret') or client_id.get('twitch_client_secret')
        elif isinstance(client_id, Config):
            tw = client_id.get('twitch') or {}
            self.client_id = tw.get('client_id')
            self.client_secret = tw.get('client_secret')
        else:
            self.client_id = client_id
            self.client_secret = client_secret
            
        self.base_url = "https://api.igdb.com/v4"
        self.headers = None  # Will be set after getting IGDB token
        self.twitch_client = None
        
        # Backfill existing artwork files into the local cache
        self._backfill_existing_files()

    def _ensure_credentials(self) -> None:
        """Ensure client credentials are set, falling back to env configuration"""
        if not self.client_id or not self.client_secret:
            try:
                tw = Config().get('twitch') or {}
                if not self.client_id:
                    self.client_id = tw.get('client_id')
                if not self.client_secret:
                    self.client_secret = tw.get('client_secret')
            except Exception as e:
                logger.debug(f"Could not load credentials from env config: {e}")
        
    async def _get_igdb_token(self) -> bool:
        """Get IGDB-specific access token via Twitch client credentials"""
        self._ensure_credentials()
        if not self.client_id or not self.client_secret:
            logger.debug("Cannot obtain IGDB token: missing Twitch client_id or client_secret")
            return False
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://id.twitch.tv/oauth2/token',
                    params={
                        'client_id': self.client_id,
                        'client_secret': self.client_secret,
                        'grant_type': 'client_credentials'
                    }
                ) as response:
                    if response.status != 200:
                        logger.error(f"Failed to get IGDB token: {await response.text()}")
                        return False
                    
                    data = await response.json()
                    igdb_token = data.get('access_token')
                    if not igdb_token:
                        logger.error("No access token in IGDB response")
                        return False
                    
                    # Set headers with new token
                    self.headers = {
                        'Client-ID': self.client_id,
                        'Authorization': f'Bearer {igdb_token}',
                        'Accept': 'application/json'
                    }
                    return True
                    
        except Exception as e:
            logger.error(f"Error getting IGDB token: {str(e)}")
            return False
    
    async def search_game(self, game_name: str) -> Optional[Dict[str, Any]]:
        """Search for a game by name
        
        Args:
            game_name: Name of the game to search for
            
        Returns:
            Optional[Dict[str, Any]]: Game info if found, None otherwise
        """
        try:
            # Get IGDB token if we don't have one
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
            # Search for the game
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/games",
                    headers=self.headers,
                    data=f'search "{game_name}"; fields name,id,artworks,cover; where category = 0;'
                ) as response:
                    if response.status != 200:
                        logger.error(f"Failed to search game: {await response.text()}")
                        return None
                    
                    games = await response.json()
                    if not games:
                        logger.info(f"No games found matching '{game_name}'")
                        return None
                    
                    # Get the first (most relevant) match
                    game = games[0]
                    game_id = game.get('id')
                    if not game_id:
                        return None
                    
                    logger.info(f"Found game: {game.get('name')} (id: {game_id})")
                    return game
                    
        except Exception as e:
            logger.error(f"Error searching game: {str(e)}")
            return None
    
    async def get_game_by_twitch_id(self, twitch_id: str, game_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get game by Twitch game ID and optionally game name
        
        Args:
            twitch_id: Twitch game ID
            game_name: Optional game name to try if Twitch ID fails
            
        Returns:
            Optional[Dict[str, Any]]: Game info if found, None otherwise
        """
        try:
            # Get IGDB token if we don't have one
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
            # First try with Twitch ID
            query = f'fields name,id,artworks,cover; where external_games.uid = "{twitch_id}" & external_games.category = 1;'
            logger.debug(f"IGDB query by Twitch ID: {query}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/games",
                    headers=self.headers,
                    data=query
                ) as response:
                    response_text = await response.text()
                    if response.status != 200:
                        logger.error(f"Failed to get game: {response_text}")
                        return None
                    
                    logger.debug(f"IGDB response: {response_text}")
                    games = await response.json()
                    
                    # If no games found by Twitch ID and we have a game name, try searching by name
                    if not games and game_name:
                        logger.info(f"No game found for Twitch ID {twitch_id}, trying by name: {game_name}")
                        query = f'search "{game_name}"; fields name,id,artworks,cover; where category = 0; limit 1;'
                        logger.debug(f"IGDB query by name: {query}")
                        
                        async with session.post(
                            f"{self.base_url}/games",
                            headers=self.headers,
                            data=query
                        ) as name_response:
                            name_response_text = await name_response.text()
                            if name_response.status != 200:
                                logger.error(f"Failed to get game by name: {name_response_text}")
                                return None
                            
                            logger.debug(f"IGDB name search response: {name_response_text}")
                            games = await name_response.json()
                    
                    if not games:
                        logger.info(f"No game found for Twitch ID {twitch_id} or name {game_name}")
                        return None
                    
                    game = games[0]
                    game_id = game.get('id')
                    if not game_id:
                        return None
                    
                    logger.info(f"Found game: {game.get('name')} (id: {game_id})")
                    return game
                    
        except Exception as e:
            logger.error(f"Error getting game: {str(e)}")
            return None
    
    async def get_artwork(self, game_id: int) -> Optional[str]:
        """Get artwork URL for a game
        
        Args:
            game_id: IGDB game ID
            
        Returns:
            Optional[str]: URL of the game artwork, or None if not found
        """
        try:
            # Get IGDB token if we don't have one
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
            # Get artwork for the game
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/artworks",
                    headers=self.headers,
                    data=f'fields image_id,width,height; where game={game_id}; sort rating desc; limit 1;'
                ) as response:
                    if response.status != 200:
                        logger.error(f"Failed to get artwork: {await response.text()}")
                        return None
                    
                    artworks = await response.json()
                    if not artworks:
                        logger.info(f"No artwork found for game {game_id}")
                        return None
                    
                    # Get the first artwork (highest rated)
                    artwork = artworks[0]
                    image_id = artwork.get('image_id')
                    if not image_id:
                        return None
                    
                    # Log artwork info
                    width = artwork.get('width', 'unknown')
                    height = artwork.get('height', 'unknown')
                    logger.info(f"Found artwork: {width}x{height} (id: {image_id})")
                    
                    # Verify the image URL is accessible with a short timeout
                    artwork_url = self._get_artwork_url(artwork['url'])
                    if not artwork_url:
                        return None
                    
                    try:
                        timeout = aiohttp.ClientTimeout(total=5)  # 5 second timeout
                        async with aiohttp.ClientSession(timeout=timeout) as session:
                            async with session.head(artwork_url) as head_response:
                                if head_response.status != 200:
                                    logger.error(f"Artwork URL not accessible: {artwork_url}")
                                    return None
                                return artwork_url
                    except asyncio.TimeoutError:
                        logger.error(f"Timeout checking artwork URL: {artwork_url}")
                        return None
                    except Exception as e:
                        logger.error(f"Error checking artwork URL: {str(e)}")
                        return None
                    
        except Exception as e:
            logger.error(f"Error getting artwork: {str(e)}")
            return None
    
    def _normalize_slug(self, name: str) -> str:
        """Convert game name to normalized lowercase slug"""
        import re
        if not name:
            return ""
        return re.sub(r'[^a-zA-Z0-9_]', '', name.strip().replace(' ', '_').lower())

    def _get_cached_record(self, twitch_id: str = None, game_name: str = None) -> Optional[Dict[str, Any]]:
        """Find cached game record in the local SQLite cache by Twitch ID, slug, or name"""
        from ...core import cache_db
        slug = self._normalize_slug(game_name) if game_name else None
        return cache_db.find_cached_game(
            twitch_id=twitch_id,
            normalized_slug=slug,
            game_name=game_name,
        )

    def _is_expired(self, record: Any) -> bool:
        """Check if cached game record has passed its 30-day TTL expiration threshold"""
        from ...core import cache_db
        return cache_db.is_expired(record)

    def cleanup_unindexed_artwork(self) -> int:
        """Remove any images from artwork_images/ that are not tracked in the cache or are orphaned raw files"""
        try:
            from ...core import cache_db
            from ...core.config import get_artwork_dir
            artwork_dir = get_artwork_dir()
            if not os.path.exists(artwork_dir):
                return 0

            # Remove unhelpful raw placeholder records like 'Game <number>'
            cache_db.delete_placeholder_games()

            valid_files = set()
            for g in cache_db.all_cached_games():
                if g.get('cover_filename'):
                    valid_files.add(g['cover_filename'])
                if g.get('artwork_filename'):
                    valid_files.add(g['artwork_filename'])

            deleted_count = 0
            for filename in os.listdir(artwork_dir):
                if not filename.endswith(('.jpg', '.png', '.jpeg', '.webp')):
                    continue
                if filename not in valid_files:
                    file_path = os.path.join(artwork_dir, filename)
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.info(f"Cleaned up unindexed/orphaned artwork file: {filename}")
                    except Exception as e:
                        logger.error(f"Error removing {filename}: {e}")

            return deleted_count
        except Exception as e:
            logger.error(f"Error during artwork cleanup: {e}")
            return 0

    def _backfill_existing_files(self) -> None:
        """Scan artwork_images directory on disk and register valid cached files into the cache"""
        try:
            from ...core import cache_db
            from ...core.config import get_artwork_dir
            artwork_dir = get_artwork_dir()
            if not os.path.exists(artwork_dir):
                return

            existing_records = cache_db.all_cached_games()
            by_slug = {r['normalized_slug']: r for r in existing_records if r.get('normalized_slug')}

            for filename in sorted(os.listdir(artwork_dir)):
                if not filename.endswith(('.jpg', '.png')):
                    continue

                slug = None
                is_cover = filename.startswith("cover_")

                if filename.startswith("cover_name_"):
                    slug = filename[11:-4]
                elif filename.startswith("name_"):
                    slug = filename[5:-4]
                elif filename.startswith("cover_art_"):
                    slug = filename[10:-4]
                elif filename.startswith("cover_") and not filename[6:-4].isdigit():
                    slug = filename[6:-4]
                elif filename.startswith("art_"):
                    slug = filename[4:-4]

                if not slug:
                    continue

                norm_slug = slug
                record = by_slug.get(norm_slug)

                if not record:
                    game_name = slug.replace('_', ' ').title()
                    record = {
                        'game_name': game_name,
                        'normalized_slug': norm_slug,
                        'cover_filename': filename if is_cover else None,
                        'artwork_filename': filename if not is_cover else None,
                        'expires_at': cache_db.fresh_expiry(),
                    }
                    cache_db.upsert_cached_game(record)
                    by_slug[norm_slug] = record
                else:
                    changed = False
                    if is_cover and not record.get('cover_filename'):
                        record['cover_filename'] = filename
                        changed = True
                    elif not is_cover and not record.get('artwork_filename'):
                        record['artwork_filename'] = filename
                        changed = True
                    if changed:
                        cache_db.upsert_cached_game(record)
        except Exception as e:
            logger.error(f"Error backfilling cached artwork files: {e}")

    async def _fetch_and_cache_game_assets(self, twitch_id: str = None, game_name: str = None) -> Optional[Dict[str, Any]]:
        """Fetch BOTH official 1080p Key Art and Box Art Cover from IGDB simultaneously and index in SQLite database"""
        if not game_name or game_name.strip() in ("Offline", "Live", "Live Stream"):
            return None
            
        from ...core import cache_db
        import aiohttp
        import shutil
        from ...core.config import get_artwork_dir
        
        artwork_dir = get_artwork_dir()
        os.makedirs(artwork_dir, exist_ok=True)
        
        try:
            logger.info(f"Fetching complete game assets (cover & key art) from IGDB for '{game_name}'")
            search_query = f'search "{game_name}"; fields name,id,artworks,cover,genres.name,summary; limit 5;'
            game_info = await self.make_request(search_query, endpoint='games')
            if not game_info:
                logger.warning(f"No games found on IGDB for '{game_name}'")
                return None
                
            # Prioritize exact name match
            game_data = None
            for g in game_info:
                if g.get('name', '').lower() == game_name.lower():
                    game_data = g
                    break
            if not game_data:
                game_data = game_info[0]
                
            found_name = game_data.get('name', game_name)
            igdb_id = game_data.get('id')
            genres = [genre.get('name') for genre in game_data.get('genres', []) if isinstance(genre, dict) and genre.get('name')]
            summary = game_data.get('summary')
            norm_slug = self._normalize_slug(found_name) or self._normalize_slug(game_name) or f"game_{igdb_id}"
            
            art_url = None
            cover_url = None
            
            # 1. Resolve Key Art URL
            if 'artworks' in game_data and game_data['artworks']:
                artwork_query = f'fields url; where id = {game_data["artworks"][0]};'
                artwork_data = await self.make_request(artwork_query, endpoint='artworks')
                if artwork_data:
                    art_url = self._get_artwork_url(artwork_data[0]['url'])
            
            # 2. Resolve Cover URL
            if 'cover' in game_data and game_data['cover']:
                cover_query = f'fields url; where id = {game_data["cover"]};'
                cover_data = await self.make_request(cover_query, endpoint='covers')
                if cover_data:
                    cover_url = self._get_artwork_url(cover_data[0]['url'])
                    
            # Cross-fallback if only one is found
            if not art_url and cover_url:
                art_url = cover_url
            elif not cover_url and art_url:
                cover_url = art_url
                
            if not art_url and not cover_url:
                logger.warning(f"No artwork or cover URL found on IGDB for '{found_name}'")
                return None
                
            timeout = aiohttp.ClientTimeout(total=12)
            if twitch_id and str(twitch_id) not in ("None", "0"):
                art_filename = f"{twitch_id}.jpg"
                cover_filename = f"cover_{twitch_id}.jpg"
            else:
                art_filename = f"name_{norm_slug}.jpg"
                cover_filename = f"cover_name_{norm_slug}.jpg"
            art_path = os.path.join(artwork_dir, art_filename)
            cover_path = os.path.join(artwork_dir, cover_filename)
            
            art_bytes = None
            cover_bytes = None
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Download Key Art
                if art_url:
                    try:
                        async with session.get(art_url) as r:
                            if r.status == 200:
                                art_bytes = await r.read()
                                with open(art_path, 'wb') as f:
                                    f.write(art_bytes)
                    except Exception as e:
                        logger.error(f"Error downloading key art for '{found_name}': {e}")
                
                # Download Cover
                if cover_url and cover_url != art_url:
                    try:
                        async with session.get(cover_url) as r:
                            if r.status == 200:
                                cover_bytes = await r.read()
                                with open(cover_path, 'wb') as f:
                                    f.write(cover_bytes)
                    except Exception as e:
                        logger.error(f"Error downloading cover for '{found_name}': {e}")

                        
            # Ensure both files exist on disk (copy if one failed/was shared)
            if os.path.exists(cover_path) and not os.path.exists(art_path):
                shutil.copyfile(cover_path, art_path)
            elif os.path.exists(art_path) and not os.path.exists(cover_path):
                shutil.copyfile(art_path, cover_path)
                
            # Upsert into the local SQLite cache
            record = self._get_cached_record(twitch_id=twitch_id, game_name=found_name)
            if not record:
                record = self._get_cached_record(game_name=game_name)

            expires = cache_db.fresh_expiry()

            has_cover = cover_bytes is not None or os.path.exists(cover_path)
            has_art = art_bytes is not None or os.path.exists(art_path)

            if not record:
                record = {}
            record['game_name'] = found_name
            record['normalized_slug'] = norm_slug
            if twitch_id and str(twitch_id) not in ("None", "0"):
                record['twitch_category_id'] = str(twitch_id)
            if igdb_id:
                record['igdb_id'] = igdb_id
            if has_cover:
                record['cover_filename'] = cover_filename
                record['cover_url'] = cover_url
            if has_art:
                record['artwork_filename'] = art_filename
                record['artwork_url'] = art_url
            if genres:
                record['genres'] = genres
            if summary:
                record['summary'] = summary
            record['expires_at'] = expires
            cache_db.upsert_cached_game(record)

            logger.info(f"Successfully cached and indexed BOTH Cover & Key Art for '{found_name}' (expires in 30 days)")
            return {
                "game_name": found_name,
                "cover_path": cover_path if has_cover else None,
                "art_path": art_path if has_art else None,
                "art_bytes": art_bytes,
                "cover_bytes": cover_bytes
            }
        except Exception as e:
            logger.error(f"Error fetching game assets from IGDB for '{game_name}': {e}")
            return None

    async def get_game_artwork(self, twitch_id: str, game_name: str, *, platform: Optional[str] = None) -> Optional[Union[str, BytesIO]]:
        """Get game artwork (banner/keyart) from database cache or IGDB

        Args:
            twitch_id: Twitch game ID
            game_name: Name of the game to search for
            platform: Provider platform of twitch_id/game_name ('twitch',
                'kick', 'youtube', 'tiktok'); None preserves the historical
                twitch assumption.

        Returns:
            BytesIO object containing the image data or None
        """
        if not game_name or game_name.strip() in ("Offline", "Live", "Live Stream"):
            return None

        from ...core.config import get_artwork_dir
        artwork_dir = get_artwork_dir()
        os.makedirs(artwork_dir, exist_ok=True)

        cached = self._get_cached_record(twitch_id=twitch_id, game_name=game_name)
        if cached and cached.get('artwork_filename'):
            art_path = os.path.join(artwork_dir, cached['artwork_filename'])
            if os.path.exists(art_path) and not self._is_expired(cached):
                logger.info(f"Using cached artwork for '{cached['game_name']}' ({cached['artwork_filename']})")
                with open(art_path, 'rb') as f:
                    return BytesIO(f.read())
            elif os.path.exists(art_path) and self._is_expired(cached):
                logger.info(f"Cached artwork for '{cached['game_name']}' expired (TTL). Refreshing from IGDB...")
            
        # Fetch both assets simultaneously
        res = await self._fetch_and_cache_game_assets(twitch_id=twitch_id, game_name=game_name)
        if res and res.get("art_bytes"):
            return BytesIO(res["art_bytes"])
        elif res and res.get("art_path"):
            try:
                with open(res["art_path"], 'rb') as f:
                    return BytesIO(f.read())
            except Exception as e:
                logger.debug(f"Could not read downloaded art_path: {e}")
        return None

    async def get_game_cover(self, twitch_id: str, game_name: str, *, platform: Optional[str] = None) -> Optional[Union[str, BytesIO]]:
        """Get game cover (box art) from database cache or IGDB

        Args:
            twitch_id: Twitch game ID
            game_name: Name of the game to search for
            platform: Provider platform of twitch_id/game_name ('twitch',
                'kick', 'youtube', 'tiktok'); None preserves the historical
                twitch assumption.

        Returns:
            BytesIO object containing the image data or None
        """
        if not game_name or game_name.strip() in ("Offline", "Live", "Live Stream"):
            return None

        from ...core.config import get_artwork_dir
        artwork_dir = get_artwork_dir()
        os.makedirs(artwork_dir, exist_ok=True)

        cached = self._get_cached_record(twitch_id=twitch_id, game_name=game_name)
        if cached and cached.get('cover_filename'):
            cover_path = os.path.join(artwork_dir, cached['cover_filename'])
            if os.path.exists(cover_path) and not self._is_expired(cached):
                logger.info(f"Using cached cover for '{cached['game_name']}' ({cached['cover_filename']})")
                with open(cover_path, 'rb') as f:
                    return BytesIO(f.read())
            elif os.path.exists(cover_path) and self._is_expired(cached):
                logger.info(f"Cached cover for '{cached['game_name']}' expired (TTL). Refreshing from IGDB...")
            
        # Fetch both assets simultaneously
        res = await self._fetch_and_cache_game_assets(twitch_id=twitch_id, game_name=game_name)
        if res and res.get("cover_bytes"):
            return BytesIO(res["cover_bytes"])
        elif res and res.get("cover_path"):
            try:
                with open(res["cover_path"], 'rb') as f:
                    return BytesIO(f.read())
            except Exception as e:
                logger.debug(f"Could not read downloaded cover_path: {e}")
        return None

    async def authenticate(self, client_id: str = None, client_secret: str = None, twitch_client=None) -> None:
        """Authenticate with IGDB API using Twitch credentials
        
        Args:
            client_id: Twitch client ID
            client_secret: Twitch client secret
            twitch_client: Optional TwitchSource instance to use for game name lookups
        """
        try:
            self.client_id = client_id or self.client_id
            self.client_secret = client_secret or self.client_secret
            self.twitch_client = twitch_client
            
            if not self.client_id or not self.client_secret:
                self._ensure_credentials()
            if not self.client_id or not self.client_secret:
                raise ConfigError("IGDB client ID or client secret not found in configuration")
                
            # Get access token from Twitch
            url = "https://id.twitch.tv/oauth2/token"
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials"
            }
            response = requests.post(url, data=data)
            response.raise_for_status()
            
            # Set access token and headers
            self.access_token = response.json()["access_token"]
            self.headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {self.access_token}"
            }
            logger.debug("Successfully authenticated with IGDB")
            
        except Exception as e:
            raise AuthenticationError(f"Failed to authenticate with IGDB: {str(e)}")

    async def make_request(self, query: str, endpoint: str = 'games') -> Optional[List[Dict[str, Any]]]:
        try:
            # Get IGDB token if we don't have one
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
            # Make the request
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/{endpoint}",
                    headers=self.headers,
                    data=query
                ) as response:
                    if response.status != 200:
                        logger.error(f"Failed to make request: {await response.text()}")
                        return None
                    
                    return await response.json()
                    
        except Exception as e:
            logger.error(f"Error making request: {str(e)}")
            return None

    async def get_game_name(self, game_id: str) -> Optional[str]:
        try:
            # First try by Twitch ID
            query = f'fields name; where external_games.uid = "{game_id}" & external_games.category = 1;'
            logger.debug(f"IGDB query by Twitch ID: {query}")
            response = await self.make_request(query)
            logger.debug(f"IGDB response: {response}")
            
            if response:
                return response[0].get('name')
                
            # If no results, try by game ID
            query = f'fields name; where id = {game_id};'
            logger.debug(f"IGDB query by game ID: {query}")
            response = await self.make_request(query)
            logger.debug(f"IGDB response: {response}")
            
            if response:
                return response[0].get('name')
                
            return None
            
        except Exception as e:
            logger.error(f"Error getting game name: {str(e)}")
            return None

    async def _get_game_artwork_data(self, game_id: int, artwork_type: str) -> Optional[List[Dict[str, Any]]]:
        try:
            # Get artwork data from IGDB
            query = f'fields *; where game = {game_id} & category = "{artwork_type}";'
            logger.debug(f"IGDB query for artwork data: {query}")
            response = await self.make_request(query, endpoint='artworks')
            logger.debug(f"IGDB response for artwork data: {response}")
            
            if not response:
                return None
                
            return response
            
        except Exception as e:
            logger.error(f"Failed to get artwork data: {str(e)}")
            return None

    def _get_artwork_url(self, artwork_url: str) -> str:
        try:
            # Get artwork URL
            if artwork_url.startswith('//'):
                artwork_url = artwork_url[2:]  # Remove leading //
            if not artwork_url.startswith('https://'):
                artwork_url = f"https://{artwork_url}"
            # Replace t_thumb with t_1080p for higher resolution
            artwork_url = artwork_url.replace('t_thumb', 't_1080p')
            logger.debug(f"Artwork URL: {artwork_url}")
            
            return artwork_url
            
        except Exception as e:
            logger.error(f"Error getting artwork URL: {str(e)}")
            return None
