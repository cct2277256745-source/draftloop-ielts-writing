# Keep C2 learning artifacts downstream of score lock

C2 represents Full Coaching and Mode A reports as content-addressed consumers of
an already validated Locked Score rather than extending the scoring pipeline or
letting a report composer derive scores. This preserves byte-stable C1 authority
and makes sparse coaching, RAG failure, or upstream review visible, at the cost of
requiring explicit source lineage and separate partial/review document states.
