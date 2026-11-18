from contextvars import ContextVar
from typing import Optional

# Context variable to store the current user's username
request_user: ContextVar[Optional[str]] = ContextVar("request_user", default=None)

# Context variable to store the delegation source (e.g. "OneSite", None for interactive)
request_delegation: ContextVar[Optional[str]] = ContextVar("request_delegation", default=None)

def set_user(username: Optional[str], delegation: Optional[str] = None):
    """Set the current user and delegation context"""
    request_user.set(username)
    request_delegation.set(delegation)

def set_delegation(delegation: Optional[str]):
    """Set the current delegation context"""
    request_delegation.set(delegation)

def get_user() -> Optional[str]:
    """Get the current user context"""
    return request_user.get()

def get_delegation() -> Optional[str]:
    """Get the current delegation context"""
    return request_delegation.get()
