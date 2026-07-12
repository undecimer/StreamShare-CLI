import os

def get_user() -> str:
    """Get the current user context (defaulting to local user)"""
    try:
        return os.getlogin()
    except Exception:
        return os.getenv('USER') or os.getenv('USERNAME') or "local"
