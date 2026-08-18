"""Application settings (tech-spec §3, E2.1)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ".env" copre l'avvio da dentro backend/ (env_file lì presente);
    # "../.env" copre il flusso documentato nel README (`.env` creato nella root
    # del repo, backend avviato con `cd backend && uvicorn ...`). In Docker Compose
    # le env vars arrivano dal container e questi file, se assenti, sono ignorati.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    RERANK_MODEL: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    AUTO_MIGRATE: bool = True
    LLM_MAX_CONCURRENCY: int = 5
    CORS_ORIGINS: str = "http://localhost:3000"
    # Temporary kill-switch: `derives` semantics need rework (see milestone1-tech-spec
    # discussion) — while False, consolidation groups skip abstraction entirely and each
    # fact is evaluated individually for updates/extends instead. Flip to re-enable.
    ENABLE_DERIVES: bool = False
    # Fase 4: classify entities onto the Concept TBox (MEMBER_OF). Kill switch.
    ENABLE_KERNEL_CLASSIFICATION: bool = True
    # Fase 5: PROMOTE Node clusters under kernel / first-level catch-alls. Kill switch.
    ENABLE_PROMOTE: bool = True
    # Two-level Concept match (doc4 §3). Exact then cosine; reuse vs near-band.
    BACKBONE_REUSE_THRESHOLD: float = 0.80
    BACKBONE_NEAR_THRESHOLD: float = 0.50
    # Two-threshold MDL baseline for genre promotion (doc4 §1). Both required (AND).
    BACKBONE_MDL_MIN_COVERAGE: int = 5
    BACKBONE_MDL_MIN_PAYLOAD: int = 2
    # Fase 7: S1 generalization up the Concept IS_A lattice. Stop before kernel catch-all.
    CONNECTIVITY_MAX_GENERALIZATION_HOPS: int = 1


settings = Settings()
