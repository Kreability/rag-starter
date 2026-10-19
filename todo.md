# RAG Audit — TODO

Findings from the RAG system audit (2026-09-26), in fix order.

## P0 — Security / tenancy

- [x] **Scope conversations to users.** (Pre-existing rows have no owner and cannot be attributed, so they are admin-only. Also fixed: `document_id` validation in `ask` was filtering Conversations, not Documents.) Add a `user` FK to `Conversation` plus a migration that backfills it. Filter the list, detail, delete and `ask` endpoints by `request.user` ([views.py:362](backend/rag/views.py#L362), [views.py:519](backend/rag/views.py#L519)).
- [x] **Stop replacing documents by name.** Replace `update_or_create(organization, name)` in the upload, bulk-upload and source endpoints. Either reject a duplicate name with 409, or allow replacement only by the uploader or an admin. Delete the old storage object when a file is replaced ([views.py:115](backend/rag/views.py#L115), [views.py:207](backend/rag/views.py#L207), [views.py:242](backend/rag/views.py#L242)).
- [x] **Close the Confluence token leak.** Remove the `CONFLUENCE_TOKEN` env fallback, or only use it when `source_uri` matches a configured `CONFLUENCE_URL` host ([ingest.py:610](backend/rag/ingest.py#L610)).
- [x] **Fix the SSRF redirect bypass.** (Done in `security.safe_get`. Residual risk: DNS rebinding between validation and connect; pin the resolved IP if this ever faces hostile tenants.) Set `allow_redirects=False` and re-validate each redirect hop manually. Re-validate the URL at fetch time, including on re-index. Add 6333, 6334 and 9001 to the blocked ports. Cap the response size ([extract.py:384](backend/rag/extract.py#L384), [security.py](backend/rag/security.py)).
- [x] **Defuse zip bombs.** Check `info.file_size` before calling `read()`, and cap both the entry count and the total uncompressed bytes ([views.py:150](backend/rag/views.py#L150)).
- [x] **Restrict delete and re-index** to the uploader or an org admin.
- [x] **Add regression tests** for each item above.

## P1 — Data integrity / retrieval quality

- [ ] **Make deletion reliable.** Don't delete the DB row if the vector delete fails; raise, or queue a retry task. Same for `_purge_existing` ([ingest.py:624](backend/rag/ingest.py#L624), [ingest.py:657](backend/rag/ingest.py#L657)).
- [ ] **Make re-index safe.** Embed and upload the new chunks first, then delete the old ones that aren't in the new ID set. Don't purge a working index when a re-index fails ([ingest.py:136](backend/rag/ingest.py#L136), [ingest.py:548](backend/rag/ingest.py#L548)).
- [ ] **Retry on provider errors.** Add `openai.APIConnectionError`, `RateLimitError` and `APITimeoutError` to the tasks' `autoretry_for` ([tasks.py:49](backend/rag/tasks.py#L49)).
- [ ] **Make summary IDs unique.** Include `document_id` and `organization_id` in the summary ID hash ([enhance.py:111](backend/rag/enhance.py#L111)), then re-index.
- [ ] **Fix the hybrid threshold.** Measure real RRF scores against the golden set. Then either skip `score_threshold` in HYBRID mode or recalibrate it ([vectordb.py:205](backend/rag/vectordb.py#L205), [conf.py:80](backend/rag/conf.py#L80)).
- [ ] **Prevent the enrichment/re-index race.** Add a per-document ingestion generation counter or lock, and have enrichment abort when it's stale.

## P2 — Operations / cost / performance

- [ ] **Raise capacity.** Increase gunicorn threads, or move streaming chat to an ASGI worker so 4 concurrent chats can't block the API ([gunicorn.conf.py](backend/gunicorn.conf.py)).
- [ ] **Fix streaming usage metering.** Set `stream_usage=True` on `ChatOpenAI` ([llm.py:27](backend/rag/llm.py#L27)) and confirm the token counts come through with a custom base URL.
- [ ] **Cut query latency.** Run language detection in parallel with rephrasing, or use `langdetect` only ([graph.py:206](backend/rag/graph.py#L206)).
- [ ] **Stop re-extracting in enrichment.** Persist image pieces during the fast pass instead of re-running Docling ([ingest.py:202](backend/rag/ingest.py#L202)).
- [ ] **Stop trusting client-supplied IPs.** Only read `X-Forwarded-For` behind a trusted proxy, or use the last hop ([views.py:327](backend/rag/views.py#L327)).
- [ ] **Fix the reaper.** Don't mark ENRICHING documents as ERROR; set them to READY with a warning. Track a heartbeat instead of relying on `modified_at` ([tasks.py:145](backend/rag/tasks.py#L145)).
- [ ] **Bound `_history`.** Fetch only the last `chat_history.limit` messages in the query ([views.py:539](backend/rag/views.py#L539)).
- [ ] **Require `SECRET_KEY` in production.** Fail at startup instead of falling back to a random key ([settings.py:14](backend/api/settings.py#L14)).
