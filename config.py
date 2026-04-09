from pydantic_settings import BaseSettings, SettingsConfigDict
 
 
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
 
    # Redis
    redis_url: str = "redis://localhost:6379/0"
 
    # VAPID keys — generate once with:
    #   python -c "from py_vapid import Vapid; v=Vapid(); v.generate_keys(); print(v.public_key, v.private_key)"
    # Or use the /generate-vapid helper endpoint (dev only)
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
 
    # Auth
    api_bearer_token: str = "dev-token"
 
    # Redis stream / key prefixes
    stream_key: str = "notifications:stream"
    stream_group: str = "workers"
    sub_key_prefix: str = "sub:"        # sub:<email>  → Hash
    topic_key_prefix: str = "topic:"    # topic:<name> → Set
    delivery_key_prefix: str = "delivery:"  # delivery:<event_id> → Hash
 
 
settings = Settings()
