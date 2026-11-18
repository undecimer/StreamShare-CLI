from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BasePlatform(ABC):
    """Base class for all platform implementations"""
    
    def __init__(self, account_id: Optional[str] = None):
        """Initialize the platform
        
        Args:
            account_id: Optional identifier for multi-account support
        """
        self.account_id = account_id or 'default'

    @abstractmethod
    async def authenticate(self) -> None:
        """Authenticate with the platform"""
        pass

    @abstractmethod
    async def post_content(self, content: Dict[str, Any]) -> str:
        """Post content to the platform
        
        Args:
            content: Dictionary containing content details
                    (url, title, image, etc.)
        
        Returns:
            str: URL or ID of the posted content
        """
        pass

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """Validate that the current credentials are valid
        
        Returns:
            bool: True if credentials are valid
        """
        pass

    def get_session_file(self, base_name: str) -> str:
        """Get the session file name for this account
        
        Args:
            base_name: Base name for the session file
            
        Returns:
            str: Session file name including account identifier
        """
        if self.account_id == 'default':
            return base_name
        return f"{base_name.rsplit('.', 1)[0]}_{self.account_id}.{base_name.rsplit('.', 1)[1]}"
