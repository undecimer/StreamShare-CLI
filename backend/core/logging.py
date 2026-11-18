import logging
import logging.handlers
import os
import sys
import re

from .context import get_user, get_delegation
from .paths import get_log_dir

class ContextFilter(logging.Filter):
    """Injects user and delegation context into log records"""
    def filter(self, record):
        user = get_user()
        delegation = get_delegation()
        if user and delegation:
            record.user = f"[{user}:{delegation}] "
            record.is_delegated = True
            record.delegation = delegation
        elif user:
            record.user = f"[{user}] "
            record.is_delegated = False
            record.delegation = None
        else:
            record.user = ""
            record.is_delegated = False
            record.delegation = None
        return True

class ColoredConsoleFormatter(logging.Formatter):
    """Clean, human-readable console log formatter with ANSI colors and compact badges"""
    
    RESET = "\033[0m"
    DIM = "\033[90m"
    BOLD = "\033[1m"
    
    LEVEL_COLORS = {
        logging.DEBUG: "\033[90mDEBUG\033[0m",
        logging.INFO: "\033[36mINFO \033[0m",
        logging.WARNING: "\033[33mWARN \033[0m",
        logging.ERROR: "\033[1;31mERROR\033[0m",
        logging.CRITICAL: "\033[1;41mCRIT \033[0m"
    }

    def format(self, record):
        # Format time as HH:MM:SS
        time_str = f"{self.DIM}{self.formatTime(record, '%H:%M:%S')}{self.RESET}"
        
        # Level badge
        lvl_str = self.LEVEL_COLORS.get(record.levelno, record.levelname)
        
        # User & Delegation badge
        user = getattr(record, 'user', '')
        if user and user.strip():
            if getattr(record, 'is_delegated', False):
                # Distinct Amber/Orange badge for Delegated tasks (e.g. [undecimer:OneSite])
                user_str = f"\033[38;5;214m{user.strip()}\033[0m "
            else:
                # Vibrant Magenta badge for User/Interactive tasks (e.g. [undecimer])
                user_str = f"\033[35m{user.strip()}\033[0m "
        else:
            user_str = ""
        
        # Short module/logger name (only for warnings, errors, or debug)
        name_str = ""
        if record.levelno >= logging.WARNING or record.levelno == logging.DEBUG:
            short_name = record.name.split('.')[-1]
            name_str = f"{self.DIM}[{short_name}]{self.RESET} "
            
        msg = record.getMessage()
        return f"{time_str} {lvl_str} {user_str}{name_str}{msg}"

class SmartAccessFilter(logging.Filter):
    """Filter out routine static asset and frontend polling endpoints from console logs"""
    
    IGNORED_PREFIXES = (
        "/static/",
        "/assets/",
        "/artwork_images/",
        "/favicon",
        "/api/status",
        "/api/ws/",
        "/api/auth/me",
        "/api/gallery",
        "/api/history",
        "/api/users",
        "/api/customizer/presets",
        "/api/customizer/config",
        "/api/customizer/logo",
        "/api/customizer/games"
    )
    
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        
        # Silence WebSocket polling & connection handshakes from cluttering console
        if "WebSocket" in msg or "connection open" in msg or "connection closed" in msg:
            return False
            
        # Check ignored GET routes
        for prefix in self.IGNORED_PREFIXES:
            if f'"GET {prefix}' in msg:
                return False
                
        # Also ignore routine page loads and dashboard polling
        if ('"GET /api/settings' in msg or 
            '"GET /api/accounts' in msg or 
            '"GET / HTTP' in msg or 
            '"GET /customizer HTTP' in msg or 
            '"GET /changelog HTTP' in msg):
            return False
            
        return True

class UvicornErrorFilter(logging.Filter):
    """Filter out noisy raw WebSocket connection handshake logs from uvicorn.error"""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Silence raw uvicorn WebSocket handshakes and connection open/closed noise
        if "WebSocket" in msg or "connection open" in msg or "connection closed" in msg:
            return False
        return True

class ColoredAccessFormatter(logging.Formatter):
    """Clean, formatted API access logs for terminal console"""
    DIM = "\033[90m"
    RESET = "\033[0m"

    # Credentials/secrets must never reach logs (they ride query strings for
    # SSO callbacks, WebSocket handshakes and <img> loads — M13).
    _SENSITIVE_QUERY = re.compile(
        r"((?:code|state|token|access_token|refresh_token|verifier|api_key|key|session_id|sig|signature)=)[^&\s]*",
        re.IGNORECASE,
    )

    @classmethod
    def _redact(cls, path: str) -> str:
        if "?" not in path:
            return path
        return cls._SENSITIVE_QUERY.sub(r"\1***", path)

    def format(self, record):
        time_str = f"{self.DIM}{self.formatTime(record, '%H:%M:%S')}{self.RESET}"
        msg = record.getMessage()
        
        m = re.search(r'"([A-Z]+)\s+([^"\s]+)\s+HTTP/[^"]+"\s+(\d+)', msg)
        if m:
            method, path, status = m.groups()
            path = self._redact(path)
            status_int = int(status)
            if status_int < 300:
                status_color = f"\033[32m{status}\033[0m"
                icon = "🟢" if method == "POST" else "⚙️" if method == "PUT" else "🗑️" if method == "DELETE" else "🌐"
            elif status_int < 400:
                status_color = f"\033[33m{status}\033[0m"
                icon = "🟡"
            else:
                status_color = f"\033[1;31m{status}\033[0m"
                icon = "🔴"
                
            return f"{time_str} \033[34mAPI  \033[0m {icon} \033[1m{method}\033[0m {path} → {status_color}"
        
        return f"{time_str} \033[34mAPI  \033[0m {msg}"

def setup_logging(debug: bool = False):
    """Configure logging for the application and web server"""
    
    log_level = logging.DEBUG if debug else logging.INFO
    
    # File Formatter (Standard format for disk log files)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(user)s%(message)s')
    
    # Console Formatter (Beautified colored format)
    console_formatter = ColoredConsoleFormatter()
    
    # Context Filter
    context_filter = ContextFilter()
    
    # --- Application Logger (Root) ---
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = []
    
    # Console Handler (Stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(context_filter)
    root_logger.addHandler(console_handler)
    
    # File Handler (streamshare.log)
    log_dir = get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    app_file_handler = logging.FileHandler(os.path.join(log_dir, 'streamshare.log'), encoding='utf-8')
    app_file_handler.setFormatter(file_formatter)
    app_file_handler.addFilter(context_filter)
    root_logger.addHandler(app_file_handler)
    
    # --- Web Server Logger (Uvicorn Access) ---
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.setLevel(logging.INFO)
    uvicorn_access_logger.handlers = []
    
    # Web File Handler (web.log - receives all raw access logs)
    web_file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    web_file_handler = logging.FileHandler(os.path.join(log_dir, 'web.log'), encoding='utf-8')
    web_file_handler.setFormatter(web_file_formatter)
    uvicorn_access_logger.addHandler(web_file_handler)
    
    # Web Console Handler (Filtered & Colored for meaningful actions only)
    web_console_handler = logging.StreamHandler(sys.stdout)
    web_console_handler.setFormatter(ColoredAccessFormatter())
    web_console_handler.addFilter(SmartAccessFilter())
    uvicorn_access_logger.addHandler(web_console_handler)
    uvicorn_access_logger.propagate = False
    
    # --- Uvicorn Error & Root Logger ---
    uvicorn_err_filter = UvicornErrorFilter()
    for u_name in ("uvicorn.error", "uvicorn"):
        u_log = logging.getLogger(u_name)
        u_log.setLevel(logging.INFO)
        u_log.handlers = []
        u_console = logging.StreamHandler(sys.stdout)
        u_console.setFormatter(console_formatter)
        u_console.addFilter(uvicorn_err_filter)
        u_log.addHandler(u_console)
        u_log.propagate = False
    
    # Silence verbose 3rd party loggers
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("instagrapi").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("TikTokLive").setLevel(logging.WARNING)
    logging.getLogger("tweepy").setLevel(logging.WARNING)
    
    return root_logger
