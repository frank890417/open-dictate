# MCP roadmap

The MCP surface is a local automation boundary for Open Dictate. It is not a
remote transcription service and does not grant an agent ambient access to the
microphone, recordings, transcripts, personal glossary, or speaker profiles.

## Product principles

- Start with a local `stdio` server launched by an MCP host. Do not listen on a
  network port in the first release.
- Default to read-only discovery. Every mutation or recording action requires
  explicit, narrowly scoped user consent at invocation time.
- Reuse the versioned daemon protocol and `ProductConfig`; MCP must not become a
  second source of truth for transcription or storage paths.
- Return the minimum necessary data. Prefer opaque IDs and summaries over raw
  audio paths, full transcripts, or personal dictionary contents.
- Keep private memory and identity implementations behind adapters. The public
  server defines capabilities and consent boundaries only.

## Phase M0 — Contract and threat model

- Publish a capability manifest and JSON Schemas for MCP inputs and outputs.
- Define stable identifiers for recordings, transcript jobs, glossary proposals,
  and diagnostics without exposing absolute filesystem paths.
- Document trust boundaries for the MCP host, local daemon, external lexicon
  adapter, filesystem, and optional model downloader.
- Add adversarial tests for path traversal, symlinks, oversized payloads,
  newline injection, prompt injection in transcripts, and concurrent mutations.

**Exit gate:** every proposed capability has an owner, data classification,
consent rule, audit event, size limit, timeout, and redaction behavior.

## Phase M1 — Local read-only server

Implement a dependency-pinned local `stdio` server after choosing an SDK with a
maintained security policy. Initial resources:

- `opendictate://status`: app, daemon, model, protocol, and health summary.
- `opendictate://capabilities`: supported commands and optional adapters.
- `opendictate://glossary/summary`: counts and schema version, not term contents.
- `opendictate://diagnostics/recent`: redacted local diagnostic summaries.

Initial tools are non-recording and non-mutating:

- `dictate_health_check`
- `dictate_list_capabilities`
- `dictate_validate_audio` for an explicitly supplied file handle or approved ID

Prompts may offer meeting-preparation and glossary-review templates, but prompt
arguments are untrusted content and never authorize a tool call.

**Exit gate:** server works with the App stopped where appropriate, has no
network listener, reveals no private paths, and passes host interoperability and
malformed-message tests.

## Phase M2 — Consent-gated actions

Add narrowly scoped tools only after the host can display informed consent:

- `dictate_transcribe_audio`: process one user-selected audio object.
- `dictate_propose_glossary_pair`: create a review item; never auto-accept it.
- `dictate_export_transcript`: export one named job to an approved destination.
- `dictate_reload_lexicon`: reload after an external, user-approved change.

Microphone capture remains an App interaction. If a future MCP tool can start
recording, each start requires foreground confirmation, shows a persistent
recording indicator, enforces a time limit, and provides an immediate stop tool.
There is no background or blanket recording consent.

**Exit gate:** cancellations stop work, mutation tools are idempotent, every
change has a local audit event, and denial leaves no partial output.

## Phase M3 — Private memory and connection adapters

Define public interfaces for optional memory lookup, glossary suggestion,
meeting context, and speaker-label providers. Private products may implement
them out of tree. The public MCP server receives only the minimal result needed
for the active invocation and must operate correctly when no adapter is present.

Adapters declare capability and schema versions. They cannot add undeclared MCP
tools dynamically, bypass consent, weaken redaction, or write to public core
storage. Compatibility is checked using the same overlay lock and release gates.

## Tool annotations and safety

Each tool declares `readOnlyHint`, `destructiveHint`, `idempotentHint`, and
`openWorldHint` where the selected MCP SDK supports them. Treat annotations as
UI and planning hints, not access control. The server independently enforces:

- allowlisted operations and bounded input sizes;
- canonical paths under approved roots with symlink checks;
- socket ownership and `0600` permissions;
- explicit destination approval for exports;
- redaction of paths, transcript text, glossary terms, model prompts, and tokens;
- local-only audit records with retention controls;
- no shell evaluation and no command strings supplied by clients.

Resources containing transcript text or glossary entries are opt-in and scoped
to an explicit object ID. They are never exposed as broad enumerable resources.

## Evaluation matrix

MCP releases require protocol conformance tests, two supported host smoke tests,
read-only snapshot tests, consent denial and cancellation tests, mutation
idempotency tests, daemon unavailable/restart tests, private-adapter absent and
incompatible tests, data-leak canaries, and latency/timeout budgets. A red-team
fixture places instructions inside a transcript and verifies that they remain
data rather than becoming tool authorization.

No MCP package is shipped until its dependencies are locked, licenses audited,
and the stdio process can be disabled or removed without affecting ordinary
Open Dictate use.
