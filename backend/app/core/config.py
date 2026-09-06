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
    # Empty = official OpenAI endpoint (SDK default). Set to reach any
    # OpenAI-compatible server instead (LM Studio, vLLM, llama.cpp, ...) —
    # e.g. http://localhost:1234/v1. The "OPENAI_" prefix stays regardless of
    # which backend actually answers: the `openai` SDK is the client, the
    # server just has to speak its wire protocol.
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    RERANK_MODEL: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    AUTO_MIGRATE: bool = True
    # Local 30B models (LM Studio) cannot finish structured JSON in 30s once a
    # few requests share the GPU; 4 in-flight + a long read timeout lets each
    # call finish instead of disconnecting mid-prefill.
    LLM_MAX_CONCURRENCY: int = 4
    LLM_CALL_TIMEOUT_SECONDS: float = 180.0
    CORS_ORIGINS: str = "http://localhost:3000"
    # Fase 4: classify entities onto the Concept TBox (MEMBER_OF). Kill switch.
    ENABLE_KERNEL_CLASSIFICATION: bool = True
    # Two-level Concept match (doc4 §3). Exact then cosine; reuse vs near-band.
    BACKBONE_REUSE_THRESHOLD: float = 0.80
    BACKBONE_NEAR_THRESHOLD: float = 0.50
    # Two-threshold MDL baseline for Concept coverage (doc4 §1). Both required (AND).
    BACKBONE_MDL_MIN_COVERAGE: int = 3
    BACKBONE_MDL_MIN_PAYLOAD: int = 1
    # Fase 7: S1 generalization up the Concept IS_A lattice. Stop before kernel catch-all.
    CONNECTIVITY_MAX_GENERALIZATION_HOPS: int = 1
    # Fase 10: post-batch judge (doc4 §5). Default on so every batch logs :JudgeRun.
    ENABLE_JUDGE: bool = True
    # ϕ_collapse for equivalent_to between sibling Concepts (doc4 §5).
    BACKBONE_COLLAPSE_THRESHOLD: float = 0.90
    # Event triage (assert/retract with provenance) in the judge. Inspects :Evento
    # nodes directly; does not depend on any other flag.
    ENABLE_EVENT_TRIAGE: bool = False
    # Waiting-events hard cap on checks_without_progress before event_triage gives
    # up and marks an event "incomplete" instead of leaving it "waiting" forever.
    PENDING_HYPOTHESIS_LISTEN_WINDOW: int = 5
    # Macrotask 7: pairwise entity↔entity decisions batched per LLM call instead
    # of one call per pair. 12–15 keeps the JSON array small enough for local
    # models (truncation risk) while cutting the O(k²) fan-out by ~10×.
    PAIR_RELATION_BATCH_SIZE: int = 15


settings = Settings()
