"""Configuration via environment variables with sensible defaults."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # Overpass API
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_rate_limit_requests: int = 2
    overpass_rate_limit_window_seconds: int = 10
    overpass_timeout: int = 30
    # Comma-separated mirrors tried (in order) when the primary Overpass endpoint fails or is overloaded.
    overpass_fallback_urls: str = "https://overpass.kumi.systems/api/interpreter"
    overpass_max_elements: int = 5000  # hard cap on elements per query (keeps responses/timeouts sane)
    # Mainstream landmarks (museums, memorials, viewpoints...) are only queried up to this radius;
    # larger searches fetch the "hidden places" classes only.
    overpass_landmark_max_radius_km: float = 15.0

    # Atlas Obscura
    atlas_obscura_base_url: str = "https://www.atlasobscura.com"
    atlas_obscura_timeout: int = 15

    # Wikidata
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"
    wikidata_timeout: int = 30

    # Cache
    cache_dir: str = "cache"
    cache_ttl_seconds: int = 86400  # 24 hours
    cache_max_entries: int = 20000  # per cache directory; oldest files are evicted beyond this

    # LLM Intelligence Layer (Epic 2)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_provider: str = "anthropic"  # 'anthropic' or 'openai'
    llm_model: str = "claude-sonnet-5-5"  # cheaper option: claude-haiku-5-5
    llm_effort: str = "low"  # thinking depth for Claude: low | medium | high | xhigh | max; "" = model default
    openai_model: str = "gpt-4.1-mini"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.7
    llm_cache_ttl_seconds: int = 604800  # 7 days for generated descriptions; 0 = keep forever
    llm_daily_token_cap: int = 0  # input+output tokens per UTC day across calls made via services/llm_client (not Deep Research); 0 = off
    llm_timeout: int = 30

    # HTTP / security
    cors_origins: str = "*"  # comma-separated origins; "*" allows any (credentials are then off)
    trusted_proxy_hops: int = 0  # reverse proxies in front (nginx = 1): trust X-Forwarded-For
    rate_limit_enabled: bool = True
    block_scanner_paths: bool = True  # short-circuit /.env, /wp-admin, ... with a bare 404
    security_headers_enabled: bool = True
    csp: str = "default"  # "default" = built-in policy, "" = disabled, anything else = verbatim

    # API
    default_radius_km: float = 5.0
    max_radius_km: float = 50.0
    max_limit: int = 200

    # Deduplication
    dedup_distance_meters: float = 50.0
    # Places whose (distinctive) names match may be merged this far apart (polygon centre vs pin).
    dedup_name_match_distance_meters: float = 150.0

    # Discovery pipeline
    discovery_deadline_seconds: float = 25.0  # answer with what has arrived by then; slow sources keep warming the cache
    discovery_source_timeout_seconds: float = 75.0  # per-source budget; a slow source is reported as "timeout"

    # Explorer Journal (Epic 5)
    journal_data_dir: str = "data/journal"
    journal_proximity_radius_m: float = 100.0
    journal_auto_detect_dwell_minutes: float = 5.0

    # Gamification (Epic 6)
    gamification_data_dir: str = "data/gamification"

    # Offline Mode (Epic 7)
    offline_data_dir: str = "data/offline"
    offline_max_region_tiles: int = 50000
    offline_max_cached_places: int = 10000
    offline_storage_limit_mb: int = 500  # max offline storage in MB

    # Community (Epic 8)
    community_data_dir: str = "data/community"

    # Route Builder (Epic 4)
    route_visit_time_minutes: float = 10.0
    route_walking_speed_kmh: float = 5.0
    route_cycling_speed_kmh: float = 15.0

    # Daily place, loop routes, ask-the-place
    daily_top_k: int = 5  # the day's pick is chosen among the K best-ranked candidates
    daily_story_max_tokens: int = 400
    loop_safety_margin: float = 0.10  # fraction of a loop's time budget kept in reserve
    place_ask_max_tokens: int = 500
    place_ask_cache_ttl_seconds: int = 604800  # 7 days
    walk_report_max_tokens: int = 600

    # Deep Research (enrichment layer)
    parallel_api_key: str = ""
    parallel_api_url: str = "https://api.parallel.ai/v1/tasks/runs"
    gemini_api_key: str = ""
    deep_research_enabled: bool = True
    deep_research_cache_ttl_seconds: int = 2592000  # 30 days
    deep_research_poll_interval: int = 10  # seconds between status polls
    deep_research_max_wait: int = 300  # max seconds to wait for research

    # Wikimedia enrichment (POST /api/place/enrich). Wikimedia asks API clients to send a
    # descriptive User-Agent with contact info: override with your own via TERRA_WIKIMEDIA_USER_AGENT.
    wikimedia_user_agent: str = (
        "TerraIncognita/0.2 (https://github.com/DoroninDobroCorp/TerraIncognita; "
        "contact: see repository issues) python-httpx"
    )
    wikimedia_timeout: int = 10
    enrichment_rate_limit_requests: int = 10  # outgoing Wikimedia calls per window
    enrichment_rate_limit_window_seconds: float = 1.0
    enrichment_commons_radius_m: int = 300  # Commons photo search radius around the place
    enrichment_max_photos: int = 6

    model_config = {"env_file": ".env", "env_prefix": "TERRA_", "extra": "ignore"}


settings = Settings()
