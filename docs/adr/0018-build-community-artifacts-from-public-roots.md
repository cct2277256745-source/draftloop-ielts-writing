# Build community artifacts only from explicit public roots

## Status

Accepted for C3.

## Context

Copying or scanning the whole working tree can disclose ignored corpora, local
rubrics, credentials, absolute paths, or frozen private RAG identities even when
the resulting runtime has RAG disabled.

## Decision

The community builder walks only named public roots and prunes private directory
classes before descending. Denied credential, database, corpus, index, and private
key files cannot enter the candidate. Allowed files receive a sorted manifest,
content hashes, fixed timestamps, fixed permissions, and deterministic ZIP
ordering. Text files pass credential, private-key, Bearer-token, and absolute-user-
path scans. Private package trees are not copied, transformed, inventoried, or
hashed.

The manifest states that private RAG, payments, teacher portal, and final frontend
are absent. C3 produces a candidate and reproducibility evidence only; publication
requires C4 acceptance and human authorization.

## Consequences

- Two equivalent builds are byte-identical.
- A denied file or leak finding cancels the candidate.
- Clean-clone/export validation remains part of the final C3 gate.
