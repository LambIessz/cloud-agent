import os
import sys
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent", ".env")
AGENT_DIR = os.path.dirname(ENV_FILE)
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from config.secrets import load_file_secrets

load_file_secrets()

class Settings(BaseSettings):
    deepseek_api_key: str
    redis_url: str
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_api_key: str | None = None
    
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra='ignore')

settings = Settings()
