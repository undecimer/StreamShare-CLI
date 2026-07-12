class StreamShareException(Exception):
    """Base exception for StreamShare"""
    pass

class AuthenticationError(StreamShareException):
    """Raised when authentication fails"""
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}

class PlatformError(StreamShareException):
    """Raised when a platform operation fails"""
    pass

class ConfigError(StreamShareException):
    """Raised when there is a configuration error"""
    pass
