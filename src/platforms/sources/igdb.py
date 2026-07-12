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
    def __init__(self, client_id: str = None, client_secret: str = None) -> None:
        """Initialize IGDB client using Twitch credentials"""
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://api.igdb.com/v4"
        self.headers = None
        self.twitch_client = None
        
    async def _get_igdb_token(self) -> bool:
        """Get IGDB-specific access token using Twitch credentials"""
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
        """Search for a game by name"""
        try:
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
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
        """Get game by Twitch game ID and optionally game name"""
        try:
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
            query = f'fields name,id,artworks,cover; where external_games.uid = "{twitch_id}" & external_games.category = 1;'
            
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
                    
                    games = await response.json()
                    
                    if not games and game_name:
                        logger.info(f"No game found for Twitch ID {twitch_id}, trying by name: {game_name}")
                        query = f'search "{game_name}"; fields name,id,artworks,cover; where category = 0; limit 1;'
                        
                        async with session.post(
                            f"{self.base_url}/games",
                            headers=self.headers,
                            data=query
                        ) as name_response:
                            name_response_text = await name_response.text()
                            if name_response.status != 200:
                                logger.error(f"Failed to get game by name: {name_response_text}")
                                return None
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
        """Get artwork URL for a game"""
        try:
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
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
                    
                    artwork = artworks[0]
                    image_id = artwork.get('image_id')
                    if not image_id:
                        return None
                    
                    artwork_url = self._get_artwork_url(artwork['url'])
                    if not artwork_url:
                        return None
                    
                    try:
                        timeout = aiohttp.ClientTimeout(total=5)
                        async with aiohttp.ClientSession(timeout=timeout) as head_session:
                            async with head_session.head(artwork_url) as head_response:
                                if head_response.status != 200:
                                    logger.error(f"Artwork URL not accessible: {artwork_url}")
                                    return None
                                return artwork_url
                    except Exception as e:
                        logger.error(f"Error checking artwork URL: {str(e)}")
                        return None
                    
        except Exception as e:
            logger.error(f"Error getting artwork: {str(e)}")
            return None
    
    async def get_game_artwork(self, twitch_id: str, game_name: str) -> Optional[Union[str, BytesIO]]:
        """Get game artwork from IGDB"""
        if twitch_id and str(twitch_id) != "None" and str(twitch_id) != "0":
            artwork_filename = f"{twitch_id}.jpg"
        else:
            import re
            slug = re.sub(r'[^a-zA-Z0-9_]', '', game_name.replace(' ', '_').lower())
            artwork_filename = f"name_{slug}.jpg"
        artwork_path = os.path.join("artwork_images", artwork_filename)
        
        if os.path.exists(artwork_path):
            logger.info(f"Using cached artwork for game {game_name}")
            with open(artwork_path, 'rb') as f:
                image_data = BytesIO(f.read())
            return image_data
            
        try:
            logger.info(f"Searching for game by name: {game_name}")
            search_query = f'search "{game_name}"; fields name,artworks,cover; limit 1;'
            game_info = await self.make_request(search_query, endpoint='games')
            
            if game_info:
                game_data = game_info[0]
                found_name = game_data['name']
                logger.info(f"Found game: {found_name}")
                
                if 'artworks' in game_data and game_data['artworks']:
                    artwork_query = f'fields url; where id = {game_data["artworks"][0]};'
                    artwork_data = await self.make_request(artwork_query, endpoint='artworks')
                elif 'cover' in game_data:
                    cover_query = f'fields url; where id = {game_data["cover"]};'
                    artwork_data = await self.make_request(cover_query, endpoint='covers')
                else:
                    logger.error(f"No artwork or cover found for game {found_name}")
                    return None
                    
                if not artwork_data:
                    return None
                    
                artwork_url = self._get_artwork_url(artwork_data[0]['url'])
                if not artwork_url:
                    return None
                
                try:
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(artwork_url) as response:
                            if response.status != 200:
                                logger.error(f"Failed to download artwork: {artwork_url}")
                                return None
                                
                            content = await response.read()
                            os.makedirs("artwork_images", exist_ok=True)
                            with open(artwork_path, 'wb') as f:
                                f.write(content)
                                
                            logger.info(f"Successfully cached artwork for game {found_name}")
                            return BytesIO(content)
                except Exception as e:
                    logger.error(f"Error downloading artwork: {str(e)}")
                    return None
            
            return None
                    
        except Exception as e:
            logger.error(f"Error getting artwork: {str(e)}")
            return None
 
    async def authenticate(self, client_id: str = None, client_secret: str = None, twitch_client=None) -> None:
        """Authenticate with IGDB API using Twitch credentials"""
        try:
            self.client_id = client_id or self.client_id
            self.client_secret = client_secret or self.client_secret
            self.twitch_client = twitch_client
            
            if not self.client_id or not self.client_secret:
                raise ConfigError("IGDB client ID or client secret not found in configuration")
                
            url = "https://id.twitch.tv/oauth2/token"
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials"
            }
            response = requests.post(url, data=data)
            response.raise_for_status()
            
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
            if not self.headers:
                if not await self._get_igdb_token():
                    return None
            
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
            query = f'fields name; where external_games.uid = "{game_id}" & external_games.category = 1;'
            response = await self.make_request(query)
            
            if response:
                return response[0].get('name')
                
            query = f'fields name; where id = {game_id};'
            response = await self.make_request(query)
            
            if response:
                return response[0].get('name')
                
            return None
            
        except Exception as e:
            logger.error(f"Error getting game name: {str(e)}")
            return None
 
    async def _get_game_artwork_data(self, game_id: int, artwork_type: str) -> Optional[List[Dict[str, Any]]]:
        try:
            query = f'fields *; where game = {game_id} & category = "{artwork_type}";'
            response = await self.make_request(query, endpoint='artworks')
            if not response:
                return None
            return response
        except Exception as e:
            logger.error(f"Failed to get artwork data: {str(e)}")
            return None
 
    def _get_artwork_url(self, artwork_url: str) -> str:
        try:
            if artwork_url.startswith('//'):
                artwork_url = artwork_url[2:]
            if not artwork_url.startswith('https://'):
                artwork_url = f"https://{artwork_url}"
            artwork_url = artwork_url.replace('t_thumb', 't_1080p')
            return artwork_url
        except Exception as e:
            logger.error(f"Error getting artwork URL: {str(e)}")
            return None
