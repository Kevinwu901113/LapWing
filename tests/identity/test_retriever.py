from __future__ import annotations

import pytest
from src.identity.retriever import IdentityRetriever, RetrievalResult
from src.identity.flags import IdentityFlags
from src.identity.auth import create_kevin_auth
from src.identity.models import ContextProfile, Sensitivity


async def test_retrieve_returns_matching_claims(populated_store):
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("test query", auth)
    assert len(result.claims) > 0
    assert result.trace is not None


async def test_retrieve_disabled_returns_empty(populated_store):
    flags = IdentityFlags(retriever_enabled=False)
    retriever = IdentityRetriever(store=populated_store, flags=flags)
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("anything", auth)
    assert len(result.claims) == 0


async def test_retrieve_killswitch_no_trace(populated_store):
    """acceptance #22: killswitch → no retrieval traces written"""
    flags = IdentityFlags(identity_system_killswitch=True)
    retriever = IdentityRetriever(store=populated_store, flags=flags)
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("anything", auth)
    assert len(result.claims) == 0
    traces = await populated_store._list_retrieval_traces()
    assert len(traces) == 0


async def test_retrieve_filters_by_confidence(populated_store):
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("test", auth, min_confidence=0.9)
    # All returned claims should have confidence >= 0.9
    for claim in result.claims:
        assert claim.confidence >= 0.9


async def test_retrieve_respects_top_k(populated_store):
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("test", auth, top_k=2)
    assert len(result.claims) <= 2


async def test_retrieve_redacts_query_for_private(populated_store):
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve(
        "sensitive query about kevin's health",
        auth,
        max_sensitivity=Sensitivity.PRIVATE,
    )
    assert result.raw_query_stored is False


async def test_retrieve_public_stores_raw_query(populated_store):
    """PUBLIC queries store the raw query text."""
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("my public query", auth, max_sensitivity=Sensitivity.PUBLIC)
    assert result.raw_query_stored is True
    assert result.trace is not None
    assert result.trace.query == "my public query"


async def test_retrieve_writes_trace_on_success(populated_store):
    """A successful retrieve writes exactly one trace to the store."""
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    await retriever.retrieve("trace check", auth)
    traces = await populated_store._list_retrieval_traces()
    assert len(traces) == 1


async def test_retrieve_disabled_writes_trace(populated_store):
    """retriever_enabled=False still writes a disabled trace."""
    flags = IdentityFlags(retriever_enabled=False)
    retriever = IdentityRetriever(store=populated_store, flags=flags)
    auth = create_kevin_auth("s1")
    await retriever.retrieve("disabled check", auth)
    traces = await populated_store._list_retrieval_traces()
    assert len(traces) == 1


async def test_retrieve_sorted_by_confidence_descending(populated_store):
    """Claims are returned sorted by confidence descending."""
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("sort test", auth)
    confidences = [c.confidence for c in result.claims]
    assert confidences == sorted(confidences, reverse=True)


async def test_retrieve_result_is_retrieval_result(populated_store):
    """Return type is always RetrievalResult."""
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("type check", auth)
    assert isinstance(result, RetrievalResult)


async def test_retrieve_restricted_sensitivity_redacts_query(populated_store):
    """RESTRICTED sensitivity also redacts the query."""
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve(
        "restricted secret query",
        auth,
        max_sensitivity=Sensitivity.RESTRICTED,
    )
    assert result.raw_query_stored is False
    assert result.trace is not None
    assert result.trace.query == "[redacted query]"


async def test_retrieve_confidence_filter_high_threshold(populated_store):
    """With min_confidence=1.0, no claims should be returned (all have 0.8)."""
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("high confidence", auth, min_confidence=1.0)
    assert len(result.claims) == 0
