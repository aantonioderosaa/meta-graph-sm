"""NL query + history for the Node/Concept layer (Macrotask 5). Prefix /graph."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import NodeQueryRequest, QueryHistoryResponse
from app.core.neo4j_client import Neo4jSessionDep
from app.models.query import NodeQueryResponse
from app.pipeline import node_query_engine, node_query_log

router = APIRouter(prefix="/graph", tags=["node-query"])


@router.post("/query", response_model=NodeQueryResponse)
async def post_node_query(
    body: NodeQueryRequest, session: Neo4jSessionDep
) -> NodeQueryResponse:
    """NL query over :Node / :Relation / :Concept — never reads :Fact."""
    return await node_query_engine.run_node_query(session, body.text)


@router.get("/queries", response_model=QueryHistoryResponse)
async def list_node_queries(
    session: Neo4jSessionDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> QueryHistoryResponse:
    items = await node_query_log.list_node_query_logs(session, limit=limit)
    return QueryHistoryResponse(items=items)


@router.get("/queries/{query_id}", response_model=NodeQueryResponse)
async def get_node_query(
    query_id: str,
    session: Neo4jSessionDep,
) -> NodeQueryResponse:
    detail = await node_query_log.get_node_query_log_detail(session, query_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"NodeQueryLog '{query_id}' not found",
        )
    return detail
