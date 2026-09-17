# Rights-gate the removable RAG sidecar

C1 treats the frozen retrieval release as an immutable external capability: the
declared target and rights controls must pass before runtime artifacts are opened,
and exact runtime validation must pass before a Dense + BM25 + RRF provider can be
constructed. Retrieval runs only after code-owned score finalization, its criterion
is advisory, its evidence is separately gated, and timeout, breaker, rights, or
package failure returns to the identical rubric-only score. This prevents private
artifact access and empirical score transfer at the cost of leaving RAG disabled
when configuration, authorization, runtime compatibility, or product utility is
not proven.
