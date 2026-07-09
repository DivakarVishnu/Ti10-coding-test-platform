import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def env(key, default=""):
    """Like os.getenv, but treats a blank value ('') the same as unset."""
    val = os.getenv(key)
    return val if val else default


class Config:
    SECRET_KEY = env("SECRET_KEY", "change-this-secret-key-in-production")

    MAX_CONTENT_LENGTH = 6 * 1024 * 1024  # 6 MB upload cap (question images)

    # Database: defaults to a local SQLite file (zero-config).
    # On Render/Railway, set DATABASE_URL to a Postgres URL for persistence
    # across deploys (SQLite files on those platforms can be wiped on redeploy).
    SQLALCHEMY_DATABASE_URI = env(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'test_platform.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    PERMANENT_SESSION_LIFETIME = timedelta(hours=6)

    # Judge0 settings. Default: Judge0's own free public instance, no key needed.
    # Swap to a self-hosted Docker instance (unlimited/free) for real exam load:
    #   JUDGE0_URL=http://<your-server>:2358   (leave RAPIDAPI_KEY blank)
    # Or RapidAPI's paid Judge0 CE if you have a subscription:
    #   JUDGE0_URL=https://judge0-ce.p.rapidapi.com , RAPIDAPI_KEY=<key>
    JUDGE0_URL = env("JUDGE0_URL", "https://ce.judge0.com")
    RAPIDAPI_KEY = env("RAPIDAPI_KEY", "")
    RAPIDAPI_HOST = env("RAPIDAPI_HOST", "judge0-ce.p.rapidapi.com")
