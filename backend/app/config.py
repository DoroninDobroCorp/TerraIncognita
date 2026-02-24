"""Configuration via environment variables with sensible defaults."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # Overpass API
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_rate_limit_requests: int = 2
    overpass_rate_limit_window_seconds: int = 10
    overpass_timeout: int = 30

    # Atlas Obscura
    atlas_obscura_base_url: str = "https://www.atlasobscura.com"
    atlas_obscura_timeout: int = 15

    # Wikidata
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"
    wikidata_timeout: int = 30

    # Cache
    cache_dir: str = "cache"
    cache_ttl_seconds: int = 86400  # 24 hours

    # LLM Intelligence Layer (Epic 2)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_provider: str = "anthropic"  # 'anthropic' or 'openai'
    llm_model: str = "claude-sonnet-4-20250514"
    openai_model: str = "gpt-4.1-mini"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.7
    llm_description_temperature: float = 0.8
    llm_cache_ttl_seconds: int = 604800  # 7 days for generated descriptions
    llm_max_conversation_turns: int = 20
    llm_timeout: int = 30

    # API
    default_radius_km: float = 5.0
    max_radius_km: float = 50.0
    default_limit: int = 50
    max_limit: int = 200

    # Deduplication
    dedup_distance_meters: float = 50.0

    # Explorer Journal (Epic 5)
    journal_data_dir: str = "data/journal"
    journal_proximity_radius_m: float = 100.0
    journal_auto_detect_dwell_minutes: float = 5.0

    # Gamification (Epic 6)
    gamification_data_dir: str = "data/gamification"
    fog_cell_size_deg: float = 0.001
    fog_default_reveal_radius_m: float = 50.0

    # Offline Mode (Epic 7)
    offline_data_dir: str = "data/offline"
    offline_max_region_tiles: int = 50000
    offline_max_cached_places: int = 10000
    offline_sync_retry_max: int = 3
    offline_storage_limit_mb: int = 500  # max offline storage in MB

    # Community (Epic 8)
    community_data_dir: str = "data/community"
    community_moderation_confirmations: int = 3

    # Route Builder (Epic 4)
    route_default_corridor_km: float = 1.0
    route_max_corridor_km: float = 10.0
    route_max_waypoints: int = 50
    route_default_max_duration_hours: float = 4.0
    route_visit_time_minutes: float = 10.0
    route_walking_speed_kmh: float = 5.0
    route_cycling_speed_kmh: float = 15.0
    route_driving_speed_kmh: float = 40.0

    # Deep Research (enrichment layer)
    parallel_api_key: str = ""
    parallel_api_url: str = "https://api.parallel.ai/v1/tasks/runs"
    gemini_api_key: str = ""
    deep_research_enabled: bool = True
    deep_research_cache_ttl_seconds: int = 2592000  # 30 days
    deep_research_poll_interval: int = 10  # seconds between status polls
    deep_research_max_wait: int = 300  # max seconds to wait for research

    model_config = {"env_file": ".env", "env_prefix": "TERRA_", "extra": "ignore"}


settings = Settings()
