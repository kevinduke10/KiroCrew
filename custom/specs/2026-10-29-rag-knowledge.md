# SPEC-0011: Company and organization RAG knowledge

## Intent

Agents retrieve from company-wide uploaded knowledge and their own organization's
knowledge. An organization can share individual documents with selected direct
children and each selected child's entire subtree, as explicitly selected by the
user. Siblings, parents and other companies receive no implicit access. Knowledge
sharing never shares agents, private conversations, credentials or private memory.

Owning contracts: [company workspaces](2026-10-29-company-workspaces.md),
[organizations](2026-10-29-organizations.md), [roles](2026-10-29-company-roles.md).
This spec owns the custom knowledge API, persistence, Bedrock adapter and browser
panel. Upstream local SQLite knowledge remains separate and unchanged.

## Acceptance criteria

- [x] SPEC-0011-AC1: Authoritative document manifests distinguish company-wide from organization-local knowledge. Company admins manage company sources; company admins and exact organization admins manage organization sources. All writes are audited and identities come from verified authentication.
- [x] SPEC-0011-AC2: Selected direct-child shares include their complete descendant subtrees. Local-only is the default. Missing, cyclic, cross-company, sibling and upward grants are rejected; recipients cannot republish inherited documents.
- [x] SPEC-0011-AC3: Bounded authenticated uploads persist original files in private S3 and submit them to a configured Bedrock Knowledge Base. Document status is explicit; retries preserve source identity. No model ID is hardcoded and no AWS deployment is claimed.
- [x] SPEC-0011-AC4: Retrieval filters by company and authorized document IDs before search, then rechecks current manifest permissions and revision before returning cited snippets. Revoked memberships/shares and stale index entries cannot authorize content. Company and organization sources can be retrieved together.
- [x] SPEC-0011-AC5: An agent-scoped retrieval endpoint requires RUN on the exact agent plus current company/organization memberships. Knowledge sharing never widens agent discovery or execution. Retrieved documents are untrusted context, not authority to invoke tools.
- [x] SPEC-0011-AC6: The web panel supports upload, downward-sharing changes, status and organization-scoped retrieval preview. Tests exercise the scope matrix, malicious/stale retrieval results, revocation, retry behavior, SDK requests and UI payloads.

- [ ] SPEC-0011-AC7: The SaaS agent worker consumes the authorized retrieval endpoint during real agent runs; deployed ingestion, isolation and revocation are verified in AWS. This product integration remains open because the SaaS worker is not implemented.

## Implementation

Use separate knowledge manifest storage and a separate Lambda role; preserve the
content API's inability to rewrite resource ACLs. A configured Bedrock Knowledge Base and S3 data source own chunking, embeddings
and vector indexing. SPEC-0012 supplies optional managed KB/data source/S3 vector
resources in the SAM template, enabled by Terraform for each environment; standalone
SAM can also reference an existing KB.
The embedding model is explicitly configured in that managed KB, never selected
by this application. Only the trusted knowledge service receives storage/index
credentials. Ingestion and retrieval adapters use injected SDK clients.

Initial uploads accept UTF-8 text/Markdown and PDF, up to 1 MiB per document.
Uploads are immutable at a caller-chosen document ID; a matching retry resumes
index submission, while a conflicting payload returns conflict. Manifest revisions
serialize sharing changes; disabling a document revokes retrieval without waiting
for vector deletion. Sharing metadata stays authoritative in DynamoDB so grant
changes do not require re-embedding. Original object paths include company and
document identity and are never taken from browser URLs.

Retrieval is bounded for the pilot, with no silent unfiltered fallback or raw
Bedrock continuation tokens. No shared positive authorization cache exists. A query
rechecks memberships and document grants before delivery. Already delivered agent
context cannot be recalled; future worker context/caches must preserve provenance
and revalidate before reuse. No SaaS worker execution loop currently exists; supply
an authenticated agent-context endpoint for its integration rather than giving
untrusted tools shared database credentials.

## Validation

AC1–AC5: `custom/tests/test_knowledge_scope.py`, `test_knowledge_api.py` and
`test_knowledge_aws.py` cover scope inheritance, role boundaries, corrupt hierarchy,
revocation during retrieval, stale/forged vectors, immutable upload retry, exact
agent RUN checks, API validation and real boto3 Stubber request shapes. AC6:
`custom/tests/browser/knowledge.test.mjs` covers upload/sharing/preview payloads,
company switches, stale responses and inert excerpt rendering.

Offline verification: 277 custom Python tests and 16 Node tests passed; SaaS mypy
with Linux platform, targeted flake8, Black/isort and SAM cfn-lint passed. No live
browser rendering, AWS deployment, production IAM or agent-worker execution is
claimed. AC7 remains open, keeping the whole feature in progress.

## Next steps

Configure the external Bedrock KB/data source using the README deployment steps,
verify live AWS behavior, and connect the future SaaS worker to scoped retrieval.
Preserve document provenance and revalidate on context/cache reuse. No commit or
push performed.
