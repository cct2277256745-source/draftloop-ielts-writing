# Separate local private research from public-safe RAG authorization

Extend ADR 0011 with explicit operator-authorized local/private research use of the
historical retrieval control format, without changing frozen bytes or their
LICENSE_UNCLEAR / PRIVATE_RESEARCH_ONLY status. Application-only adapters validate
the release and feed the existing post-score evidence/coaching boundary; this is
not scoring-pipeline integration or a product-utility promotion decision.

LOCAL_PRIVATE_RESEARCH_AUTHORIZED never implies PUBLIC_SAFE_AUTHORIZED: the public
target policy remains fail-closed and unchanged. Query encoding uses fixed local
model weights, no remote provider; private package text, linkage and hashes must
not enter public status projections, community artifacts or source-controlled
fixtures. Unsetting the package path revokes retrieval, preserving the locked score.
