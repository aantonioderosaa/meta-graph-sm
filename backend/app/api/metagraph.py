"""Metagraph layer REST views (identities, contradictions, S1, judge, incompleteness)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import (
    ConnectivityRuleListResponse,
    ContradictionListResponse,
    EventIncompletenessListResponse,
    IdentityItem,
    IdentityListResponse,
    JudgeRunListResponse,
    UnlinkFacetRequest,
    UnlinkFacetResponse,
)
from app.core.neo4j_client import Neo4jSessionDep
from app.pipeline import metagraph_layer

router = APIRouter(prefix="/graph", tags=["metagraph-layer"])


@router.get("/identities", response_model=IdentityListResponse)
async def list_identities_endpoint(session: Neo4jSessionDep) -> IdentityListResponse:
    return await metagraph_layer.list_identities(session)


@router.get("/identities/{uri}", response_model=IdentityItem)
async def get_identity_endpoint(uri: str, session: Neo4jSessionDep) -> IdentityItem:
    return await metagraph_layer.get_identity(session, uri)


@router.post("/identities/{uri}/unlink", response_model=UnlinkFacetResponse)
async def unlink_identity_facet_endpoint(
    uri: str,
    body: UnlinkFacetRequest,
    session: Neo4jSessionDep,
) -> UnlinkFacetResponse:
    """Detach a facet from an identity. Does not delete the ``:Node``."""
    return await metagraph_layer.unlink_identity_facet(
        session, uri, body.facet_node_id
    )


@router.get("/contradictions", response_model=ContradictionListResponse)
async def list_contradictions_endpoint(
    session: Neo4jSessionDep,
) -> ContradictionListResponse:
    return await metagraph_layer.list_contradictions(session)


@router.get("/connectivity-rules", response_model=ConnectivityRuleListResponse)
async def list_connectivity_rules_endpoint(
    session: Neo4jSessionDep,
) -> ConnectivityRuleListResponse:
    return await metagraph_layer.list_connectivity_rules(session)


@router.get("/judge-runs", response_model=JudgeRunListResponse)
async def list_judge_runs_endpoint(session: Neo4jSessionDep) -> JudgeRunListResponse:
    return await metagraph_layer.list_judge_runs(session)


@router.get("/event-incompleteness", response_model=EventIncompletenessListResponse)
async def list_event_incompleteness_endpoint(
    session: Neo4jSessionDep,
) -> EventIncompletenessListResponse:
    """List incomplete events. Read-only; does not require ENABLE_EVENT_TRIAGE."""
    return await metagraph_layer.list_event_incompleteness(session)
