import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def env(key, default=""):
    """Like os.getenv, but treats a blank value ('') the same as unset."""
    val = os.getenv(key)
    return val if val else default


def _normalize_db_url(url):
    """Render/Heroku give postgres:// but SQLAlchemy 2.x needs postgresql://"""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = env("SECRET_KEY", "change-this-secret-key-in-production")

    MAX_CONTENT_LENGTH = 6 * 1024 * 1024  # 6 MB upload cap (question images)

    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        env("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'test_platform.db')}")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    PERMANENT_SESSION_LIFETIME = timedelta(hours=6)

    JUDGE0_URL = env("JUDGE0_URL", "https://ce.judge0.com")
    RAPIDAPI_KEY = env("RAPIDAPI_KEY", "")
    RAPIDAPI_HOST = env("RAPIDAPI_HOST", "judge0-ce.p.rapidapi.com")