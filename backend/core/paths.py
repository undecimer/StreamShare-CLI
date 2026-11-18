import os


def get_backend_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_repo_root() -> str:
    return os.path.dirname(get_backend_root())


def get_data_dir() -> str:
    custom = os.environ.get("STREAMSHARE_DATA_DIR")
    if custom:
        return os.path.abspath(custom)
    return os.path.abspath(os.path.join(os.getcwd(), "data"))


def get_database_path() -> str:
    return os.path.join(get_data_dir(), "streamshare.db")


def get_secret_path() -> str:
    return os.path.join(get_data_dir(), "jwt_secret.txt")


def get_logos_dir() -> str:
    return os.path.join(get_data_dir(), "logos")


def get_log_dir() -> str:
    if os.environ.get("STREAMSHARE_DATA_DIR"):
        return os.path.join(get_data_dir(), "logs")
    return os.getcwd()
