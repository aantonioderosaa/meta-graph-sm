"""Event-graph settings — local copy bound to this package (D6)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class EventGraphSettings(BaseSettings):
    """Isolated settings. Same env-file layout as the legacy Settings class,
    but a distinct type with event-graph keys (piano sez. 21).
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    EVENT_GRAPH_TARGET_CHUNK_WORDS: int = 350
    EVENT_GRAPH_MAX_CHUNK_WORDS: int = 500
    EVENT_GRAPH_LLM_TIMEOUT: float = 300
    EVENT_GRAPH_LLM_CONCURRENCY: int = 4
    # Hard cap on generated tokens per call. A normal chunk's factsheet fits well
    # under this; a runaway generation stops here and fails fast (LLMValidationError,
    # not retried) instead of grinding to the read timeout and back off 5x.
    EVENT_GRAPH_LLM_MAX_TOKENS: int = 6000
    EVENT_GRAPH_EXTRACTION_MAX_CALLS: int = 3
    EVENT_GRAPH_TEMPORAL_MAX_CANDIDATES: int = 10
    EVENT_GRAPH_FLASH_MODE: bool = False
    # Livello ancore (`AncoraTemporale`) is the sole temporal owner.
    # True (default): extract / identity / line / assign, then
    # persisti_livello_ancore. False: no LLM temporal, no AncoraTemporale
    # writes, no temporal_placement (the ClusterTemporale path stays dead and
    # is not re-enabled).
    EVENT_GRAPH_TEMPORAL_ENABLED: bool = True
    CORS_ORIGINS: str = "http://localhost:3000"


settings = EventGraphSettings()


def reset_settings(**overrides: object) -> EventGraphSettings:
    """Rebuild the module-level settings object (for tests)."""
    global settings
    settings = EventGraphSettings(**overrides)  # type: ignore[arg-type]
    return settings
