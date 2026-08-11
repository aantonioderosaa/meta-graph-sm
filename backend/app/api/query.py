"""Natural-language query stub (tech-spec §9, E2.5)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import QueryRequest
from app.models.extraction import FactType
from app.models.query import FactUsed, QueryResponse, Subgraph, SubgraphNode, SubgraphRelationship

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def post_query(body: QueryRequest) -> QueryResponse:
    """Stub: return a schema-valid query response."""
    _ = body.type_filter
    return QueryResponse(
        answer=f"Stub answer for: {body.text}",
        facts_used=[
            FactUsed(
                id="fact-a",
                text="Alice works at Acme Corp.",
                source_doc_id="doc-stub-1",
            )
        ],
        subgraph=Subgraph(
            nodes=[
                SubgraphNode(
                    id="fact-a",
                    label="Fact",
                    properties={"text": "Alice works at Acme Corp.", "type": FactType.fact.value},
                )
            ],
            relationships=[
                SubgraphRelationship(source="fact-a", target="fact-b", type="extends"),
            ],
        ),
    )
