import logging
import sys

def setup_logging(debug: bool = False):
    """Configure logging for the application"""
    
    # Determine log level
    log_level = logging.DEBUG if debug else logging.INFO
    
    # Create formatters
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    
    # --- Application Logger (Root) ---
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers to avoid duplicates
    root_logger.handlers = []
    
    # Console Handler (Stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File Handler (streamshare.log)
    app_file_handler = logging.FileHandler('streamshare.log')
    app_file_handler.setFormatter(formatter)
    root_logger.addHandler(app_file_handler)
    
    # Silence other noisy loggers
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("instagrapi").setLevel(logging.INFO)
    
    return root_logger
