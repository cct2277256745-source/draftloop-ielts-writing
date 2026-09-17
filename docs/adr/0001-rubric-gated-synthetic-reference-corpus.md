# Keep rubric-gated synthetic references in a separate corpus

Synthetic References are stored and retrieved separately from Candidate Scripts, official/public samples, teacher documents, and provenance-unknown corpus material. Every synthetic record retains `synthetic: true`, generation and verification provenance, criterion profiles, and review status; generation stops when the versioned Rubric Schema is unavailable. This sacrifices immediate corpus volume, but prevents synthetic management labels from being mistaken for examiner-verified scores and keeps later RAG calibration auditable.
