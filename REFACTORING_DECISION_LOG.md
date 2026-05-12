# Refactoring Decision Log

Records **why** decisions were made that deviate from or extend the refactoring
docs. When a decision changes the plan, update the strategy/workflow docs to
reflect the new reality — then log the reasoning here so future readers know
why the docs changed.

Source abbreviations:
- STRATEGY = `docs/refactoring/REFACTORING_STRATEGY_v1.md`
- WORKFLOW = `docs/refactoring/REFACTORING_DEV_WORKFLOW.md`

---

## Phase 0 — Scaffold + import guard + CI wiring (2026-04-21)

**1. The guard ignores files outside the four new layer roots.**
Files under `openrag/components/`, `openrag/routers/`, `openrag/models/`,
`openrag/config/`, `openrag/utils/` are skipped.
- Why: Phase 0's verification requires existing tests to keep passing. If the
  guard ran against legacy code, every old import that doesn't fit the new
  rules would trip the check and block the phase. Legacy code gets migrated in
  Phases 5–12 and the guard picks those files up as they move into the new
  layer roots.
- Alternative considered: whitelist-only enforcement on new code (same idea,
  different framing). What we chose is "enforce wherever the file lives in one
  of the four roots", which is simpler.

**2. Split CI into `layer_guard.yml` + extending existing `lint.yml` and
`unit_tests.yml`, instead of one new `refactor-ci.yml`.**
WORKFLOW's CI example is a single file with three jobs (`unit-tests`,
`layer-guard`, `docker-build`). We took a different shape.
- Why: We already have a well-set-up `unit_tests.yml` and `lint.yml`. Creating
  a parallel `refactor-ci.yml` with its own unit-tests job would duplicate the
  uv setup and caching. Extending the existing files adds a few lines of
  config and reuses everything.
- Alternative considered: follow the WORKFLOW example literally. Rejected for
  the duplication reason above. Trade-off is that refactor-specific CI isn't
  all in one file.

**3. `docker-build` CI check NOT wired in Phase 0.**
WORKFLOW lists it as a required check.
- Why: Existing `build.yml` and `build_dev.yml` workflows push images to ghcr,
  which isn't what we want on every refactor push. A lightweight "docker build
  only, don't push" check needs a new job. Deferred to keep Phase 0 scope
  tight. Docker build was verified locally on the phase-0 tree.
- Alternative considered: add the job in this phase. Rejected for scope.
  Follow-up: add a `docker-build` job in a separate PR, modelled on the
  WORKFLOW CI example.

**4. Decision log policy: log reasoning, update docs.**
When a decision deviates from the strategy/workflow docs, update the docs to
match reality, then record the reasoning here.
- Why: The docs should always reflect the current plan. The log captures
  why the plan changed, not what the plan is.

---

## Phase 5 — Core domain logic for retrieval, chunking, prompts (2026-04-29)

**1. New `RetrievalSearcher` port in `core/retrieval/searcher.py`, separate from
the narrow `VectorStore` ABC.**
The retriever needs four operations (search by query string, multi-query
search, related-chunk lookup, ancestor lookup) that the Phase-4
`VectorStore` ABC does not cover — that ABC is intentionally narrow
(`search(embedding, top_k)`). We added a transitional ABC the retriever
depends on, implemented by `services/storage/milvus_ray_shim.py` over the
legacy Ray actor.
- Why: STRATEGY §5A says the retriever should "call `VectorStore.search()`
  (port method), not `vectordb.async_search.remote()`". But the legacy Ray
  actor's `async_search` takes a query *string* and embeds internally; the
  narrow `VectorStore.search(embedding, ...)` ABC doesn't fit. Pre-embedding
  in the shim before calling Ray is impractical because the actor also owns
  BM25 and surrounding-chunks semantics. A retrieval-facing port keeps the
  retriever clean of Ray today and survives Phase 7 — when the Vectordb
  god object is decomposed, these methods either move onto a richer
  `VectorStore` or split between `VectorStore` and `ChunkRepository`.
- Alternative considered: extend `VectorStore` with the four legacy methods.
  Rejected — bloats the ABC with operations that should not exist past
  Phase 7. Also considered: skip the new core port and have the retriever
  call the Ray actor through the shim with the legacy method names —
  rejected because that leaks legacy method names into core/ and makes the
  retriever harder to test.

**2. Skipped: bringing up integration tests for the new code.**
Phase 5 ships pure-domain unit tests only (50 new tests in `core/`, no Ray /
Milvus / real LLM). The new pipeline is dormant until Phase 8 wires it.
- Why: Mode 2 forbids touching the legacy wiring; the new pipeline has
  nowhere to be plugged in yet. Integration coverage will land with Phase 8
  orchestrators (or Phase 7 storage if it goes first).
- Alternative considered: stand up a fake searcher in an integration
  fixture and run a full retriever-pipeline-RRF round trip. Defers the
  same coverage to Phase 8 with less code; not worth the extra fixtures.

**3. `Query`, `SearchQueries`, `TemporalPredicate` lifted into
`core/models/query.py`.**
The legacy `components/pipeline.py` defined these inline. The new
`RetrieverPipeline` consumes them — they're domain types, not pipeline
internals.
- Why: STRATEGY §2 calls these out as `pipeline.py SearchQueries → core/models/query.py`.
- Alternative considered: keep them in `core/retrieval/`. Rejected — they
  describe a query in the abstract; the orchestrator (Phase 8) and the API
  layer will both use them, not just retrieval.

**4. Phase 5.15 (re-export shims) deferred to follow-up — then completed.**
The first Phase 5 commits (5A/5B/5C, 2026-04-29) created the new core/
modules but left the legacy `components/` files intact. STRATEGY §4.1
mandates a three-step move — create new file, update old file to
re-export from new, update consumers — and Phase 5 step 5.15 says
"Update old files to re-export from core/". We skipped that.
The follow-up sweep (2026-05-05) replaced six legacy files with shims:

| Legacy file | Shim strategy |
|---|---|
| `components/indexer/chunker/utils.py` | Plain re-export from `core.chunking.markdown_utils` |
| `components/prompts/prompts.py` | `load_prompt(key)` adapter calling `core.prompts.template_loader.load_template_by_key` |
| `components/utils.py:format_context` + `format_web_context` | Adapters into `core.prompts.chat_prompt_builder` (rest of utils stays — Phase 6+ scope) |
| `components/indexer/chunker/chunker.py` | `BaseChunker` / `RecursiveSplitter` delegate to `core.chunking.RecursiveSplitter` via Document↔ProcessedDocument↔Chunk conversion. `ChunkContextualizer` + `ChunkerFactory` retained (5D + Phase 8). |
| `components/retriever.py` | `Single`/`MultiQuery`/`HyDe` retrievers wrap `core.retrieval.retriever` strategies. Ray actor → `MilvusRayShim`; `ChatOpenAI` → `_LangChainLLMAdapter`. `RetrieverFactory` retained. |
| `components/pipeline.py` | `Query`/`SearchQueries`/`TemporalPredicate` re-exported from `core.models.query`. `RetrieverPipeline` delegates to `core.retrieval.pipeline.RetrieverPipeline` via a `_LegacyRerankerAdapter` bridging the legacy reranker (Document-in / Document-out) to the core ABC (str-in / `(idx, score)`-out). `RagPipeline` + `RAGMODE` retained (Phase 8). |

- Why: STRATEGY §4.1 is explicit ("Update old file to re-export from new
  location"); leaving the duplication in place would let the codepaths
  drift. Three CodeRabbit fixes from PR #352 (image_caption ChunkType,
  page-marker semantics, chunk_table header-only flush) had to be applied
  twice or only fixed in core — exactly the failure mode 5.15 prevents.
  The shim pattern matches the prior-art shims for config (1329cc18) and
  exceptions (a0f3d9f2).
- Alternative considered: leave the copies until Phase 8 cutover. Rejected
  — the doc explicitly puts 5.15 *inside* Phase 5, and one round of
  drift already happened.
- Side effect: a noqa side-effect import in `chunker.py` keeps the legacy
  `components.utils` ↔ `components.indexer.utils.files` circular-import
  resolving in the right order. Removed once `components.utils` is split
  in Phase 6+.
- Behavioral note: image elements in the legacy chunker now stamp
  `chunk_type=image_caption` (matching `core.models.chunk.ChunkType`)
  instead of the previous raw `image`. No legacy reader filters on this
  value, so the change is invisible to consumers.

**5. `core/chunking/recursive.py` keeps `langchain.text_splitter.RecursiveCharacterTextSplitter` as a dependency.**
STRATEGY §3 lists core as "stdlib + pydantic + pure libs" and §4.7 limits
LangChain in core to boundary converters (`from_langchain` / `to_langchain`
on domain models). The chunker imports `RecursiveCharacterTextSplitter`
directly, which is neither stdlib nor a boundary converter.
- Why: `RecursiveCharacterTextSplitter` is a self-contained recursive
  separator-based string splitter — no IO, no LLM client, no Document
  semantics. Reimplementing it in core/ would be a meaningful chunk of
  pure code with no behavior change, and the legacy chunker has been
  using it for two years with stable output. Keeping it in for Phase 5
  preserves byte-for-byte chunk equivalence, which the strangler-fig
  shim relies on for behavior parity. The import is also deferred
  (inside the constructor / `split_text`) so importing the module
  without LangChain installed doesn't fail.
- Alternative considered: write a stdlib-only recursive splitter as part
  of Phase 5. Rejected as scope creep — would couple a behaviorally
  risky rewrite (chunk boundaries shift, downstream embedding output
  changes) to the additive Phase 5 cut, breaking the parity guarantee
  the legacy shims rely on. Tracked as a Phase 12 / post-cutover
  follow-up: replace the splitter with a stdlib implementation behind
  the same `Callable[[str], int]` length-function injection point.
- Scope: limited to `RecursiveCharacterTextSplitter`. No other LangChain
  symbol leaks into core; `langchain_core.documents.Document` only
  appears inside `Chunk.from_langchain` / `Chunk.to_langchain` /
  `Document.from_langchain` / `Document.to_langchain`, all with deferred
  imports, exactly as §4.7 prescribes.

**6. Two file-layout deviations from STRATEGY §3 / §5A / §5B.**
- `core/chunking/markdown_section.py` (§5B, line 1038; §3, line 387)
  → renamed to `core/chunking/markdown_utils.py`. The contents are pure
  parsing helpers — `MDElement`, `split_md_elements`, `chunk_table`,
  `parse_markdown_table`, `get_chunk_page_number` — not a
  section-aware chunker strategy. The name `markdown_utils.py` matches
  the module's role (utilities consumed by `RecursiveSplitter`); the
  separate `markdown_section.py` / `markdown_layout.py` *strategies*
  listed in §3's tree aren't built in Phase 5 and remain available
  filenames if/when those strategies land.
- `core/retrieval/hydration.py` (§5A, line 1027) → kept as private
  `_expand_with_related_chunks` in `retriever.py`. The function is
  ~60 LOC, only invoked by `BaseRetriever.expand_search_results`, and
  splitting it would add an import + test fixture surface without any
  reuse benefit. If a second consumer ever appears (Phase 8 likely),
  promoting it to a module-level public function in `hydration.py` is
  a one-commit move.
- Why: both deviations make the module names track the actual contents
  rather than the strategy doc's pre-write naming guess. Recording so
  future readers don't grep for files that aren't there.
- Alternative considered: rename to match the strategy doc verbatim.
  Rejected — the strategy filenames anticipated different content
  (a section/layout chunker, a standalone hydration entry point) than
  what Phase 5 actually produced.

---

## Phase 1 — Registry + Exceptions (2026-04-21)

**1. Exceptions keep HTTP status_code on the class (OpenRAG style), not in
a separate error handler mapping (mandragora style).**
- Why: Existing code reads `exc.status_code` in multiple places. Switching
  to a pure domain exception + API-layer mapping dict would require changing
  every consumer now, which is unnecessary churn in Phase 1.
- Alternative considered: mandragora's pattern (bare exceptions in core/,
  status code mapping in api/error_handlers.py). Cleaner for hexagonal
  purity but rejected for backward compatibility.
- Follow-up: strip status codes from core exceptions in Phase 10 when
  `api/error_handlers.py` is built. The error handler will own the mapping.

---

## Phase 5D — Indexing domain logic + parsers (2026-04-30)

**1. `core/indexing/validators.py` is fully framework-free.**
- All FastAPI types removed (`Form`, `UploadFile`, `HTTPException`,
  `status`, `Depends`); validators are pure functions on `str` / `dict`
  / `Iterable[str]`.
- `accepted_formats` / `accepted_mimetypes` are passed as args instead
  of read from Hydra `config` at module import (legacy module-level reads
  of `ACCEPTED_FILE_FORMATS` / `DICT_MIMETYPES` /
  `FORBIDDEN_CHARS_IN_FILE_ID` are gone).
- `ValidationError` accepts a `status_code` (and `code`) kwarg. Phase 1
  hardcoded 422; the original validators raised HTTP 400 (invalid
  `file_id` / metadata JSON) and HTTP 415 (unsupported format), so a
  status-code override is needed to preserve those codes from a
  pure-domain exception. Existing precedent in the same module
  (`LLMParsingError` overrides `status_code` after `super().__init__`)
  shows the pattern is already accepted.
- HTTP translation flows through the existing global
  `openrag_exception_handler` (`@app.exception_handler(OpenRAGError)`)
  wired in Phase 1, not local `HTTPException` raises in routers.
- Why: A core module that imports FastAPI or reaches into Hydra is not
  framework-free, blocks reuse from non-HTTP entry points, and
  re-introduces the boundary violation the refactor exists to fix.
  Stripping only `Depends()` — the literal task description — would
  leave the boundary half-broken.
- Trade-off: error body becomes `{"detail": "[CODE]: msg", "extra": {}}`
  instead of FastAPI's `{"detail": "..."}` — matches every other
  `OpenRAGError`.
- Alternatives considered: (a) consolidate everything on 422 — rejected,
  observable behaviour change; (b) introduce specific subclasses
  (`UnsupportedFileFormatError`, etc.) — rejected as premature, only two
  call sites need non-default codes today (Phase 10's API error-handler
  layer can re-evaluate); (c) keep module-level Hydra reads — rejected,
  embeds the infrastructure config object into core; (d) catch and
  re-raise as `HTTPException` in the router wrappers — rejected,
  duplicates the global handler.

**2. Exception shims under `utils/exceptions/` use `core.X`, not `openrag.core.X`.**
The legacy shims imported via `openrag.core.utils.exceptions`. With both
`/app` and `/app/openrag/` reachable, Python loads the same file as two
distinct modules, producing two distinct `OpenRAGError` classes —
`isinstance` failed and the global handler never fired.
- Why: Unifying on the bare `core.X` path matches `pythonpath = ./openrag`
  and the relative-imports-within-`core/` convention (commit 4528c71).
- Follow-up: ~20 other `from openrag.X` imports across `core/`, `config/`,
  and components are latent dual-import traps and should be migrated
  in a separate pass.

**3. Parser layering: native in core, services-backed in services/workers, type-marker bases without vendor names, DI for pools.**
- Native-bytes parsers (PyMuPDF, html_to_markdown, chardet, image) live
  in `core/indexing/parsers/`. Service-/Ray-backed parsers (Marker,
  LocalWhisper) live in `services/workers/parsers/`.
- Empty marker subclasses `BasePooledParser` / `BaseClientParser` in
  `core/indexing/parsers/document_parser.py` categorize parsers by *how*
  they get their work done (actor-pool vs HTTP-client) without naming
  the implementation. A core base class called `RayPoolParser` or
  `OpenaiClientParser` would leak vendor/infrastructure into the
  framework-free layer and foreclose swapping the backend.
- Core facades (`MarkerParser`, `LocalWhisperParser`, `ClientPdfParser`,
  `ClientAudioParser`) accept any pool/client of the appropriate marker
  type via `__init__`; services own the actor lifecycle.
- Why: `@ray.remote` decoration imports infrastructure at
  class-definition time and can't be hidden behind a port. DI keeps
  facades testable with in-memory fakes.
- Alternatives considered: (a) all parsers in core with Ray injected
  via DI — rejected, class-level decoration can't be deferred to
  composition; (b) have core facades resolve the actor by name
  themselves — rejected, couples core to Ray's named-actor registry.

**4. Image preprocessing helpers extracted to `core/indexing/image_preprocessor.py`.**
Pure helpers (`ensure_png_compatible_mode`, `pil_to_png_bytes`,
`pil_to_base64`, `is_http_url`, `is_data_uri`, `HTTP_IMAGE_PATTERN`,
`DATA_URI_IMAGE_PATTERN`, `MIN_IMAGE_PIXELS`). Used by the core image
parser and by Marker captioning in services.
- Why: Both layers need PNG normalization and markdown image-reference
  detection. Sharing via core (no VLM, no langchain imports) avoids
  services depending on `components/indexer/loaders/base.py`.
- Alternative considered: leave helpers in
  `components/indexer/loaders/base.py`. Rejected —
  services-importing-components is a layering violation, and `base.py`
  drags in langchain.

**5. `services/workers/ray_utils.py` keeps function and decorator forms together; `description=` is a format-string template.**
- `call_ray_actor_with_timeout` / `@with_timeout` and `retry_with_backoff`
  / `@with_retry` (with jitter) live in one module — STRATEGY's proposed
  `_retry.py` / `_timeout.py` split for `services/inference/` doesn't
  apply here because workers need both forms in practice (decorator at
  class-definition for static-param call sites, function form for
  callsite-resolved values). The decorators delegate to the function
  form internally; splitting across two files would duplicate that
  wiring.
- `description=` accepts a **format string** like
  `"PDF parse ({file_path})"`; `_resolve_description` binds it via
  `inspect.signature.bind` against the wrapped call's args at call
  time. **Callables (lambdas) are NOT supported** — they fall through
  to `if "{" not in template:` and raise `TypeError: argument of type
  'function' is not iterable`. (One outlier in `marker_workers.py` used
  a lambda and was fixed in Phase 5E.)
- Inline `call_ray_actor_with_timeout(worker.X.remote(...))` calls in
  workers are extracted into one-line `@with_timeout`-decorated helper
  methods (`_transcribe_chunk`, `_check_pool_broken`,
  `_reset_worker_pool`, `_run_chunk`, `_convert_pdf`) returning the
  `ObjectRef`; the decorator awaits it with timeout. Worker files use
  only decorator form — no mixed styles.
- Retry-around-timeout semantics preserved: `@with_retry` outer,
  `@with_timeout` inner — `TimeoutError` propagates from the inner
  helper and the outer decorator re-runs the whole method body (slot
  pick, fresh `.remote()`, fresh timeout).
- Alternatives considered: (a) mirror inference's `_retry.py` /
  `_timeout.py` split verbatim — rejected, adds files that just import
  from each other; (b) keep description static, drop to function form
  when dynamic — rejected, re-introduces the verbose
  `call_ray_actor_with_timeout(...)` call sites the decorator was meant
  to remove; (c) keep function form for the inline cases — rejected,
  leaves a mix of styles in the same file with no clear rule.

**6. `ray_utils` canonical home moved from `components/` to `services/workers/`.**
`components/ray_utils.py` is now a back-compat shim re-exporting from
`services.workers.ray_utils`.
- Why: Ray-actor concurrency primitives belong in the services layer,
  not in `components/` (which is on the deprecation path). Routers and
  pipeline still import via the components shim during the transition.
- Follow-up: migrate the remaining `components.ray_utils` imports
  (pipeline, search router, indexer router, workspaces router, indexer
  utils) and delete the shim in Phase 5E.

**7. Docling and DoclingV2 PDF backends deferred — not migrated in Phase 5D.**
No `core/indexing/parsers/pdf/docling*` modules will be created in this
pass. Legacy `DoclingLoader` and `DoclingLoader2` stay where they are
for now.
- Why: This is a PDF backend we haven't used or tested recently —
  porting it now would pin a stale integration into the new layer. We'll
  revisit and re-port it (or drop it) in a later pass once the refactor
  has shaken out and we know whether Docling is still wanted.
- Alternative considered: port now alongside Marker / OpenAI / DotsOCR
  for completeness. Rejected — moves dead-feeling code into the new
  layer without verifying it still works.
- Follow-up: revisit during a later parser-coverage sweep. If the
  decision is to drop, the legacy modules get deleted in Phase 5E rather
  than shimmed.

**8. `ImageBlock` is the parser↔caption contract — captioning is a downstream stage's job.**
- Every parser (Image, Markdown, Docx, Pptx, Eml, Marker,
  `DotsOCRPdfClient`) emits `ImageBlock` with `caption=None`. The
  caption stage fills it in. For VLM-PDF specifically, the picture-bbox
  crop becomes an `ImageBlock(image_bytes=…, page_number=N)` — the
  parser never issues the second VLM call. One uniform contract beats
  per-parser carve-outs; the chunker sees the same `ImageBlock` shape
  from every parser, including `DotsOCRPdfClient`.
- `ImageBlock.metadata['markdown_ref']` holds the in-text placeholder
  (data-URI, `![](pptx-image-N)`, `![](docx-image-N)`,
  `![](marker-key)`); the caption stage `str.replace`s it. No
  placeholder ⇒ no `markdown_ref` ⇒ caption stage emits a
  free-standing `TextBlock`. Contract is documented on `ImageBlock`
  itself.
- `ImageBlock` carries `image_bytes` (default `b""`) AND `source_url`.
  Locally-extracted images set bytes; HTTP refs (`![alt](https://…)`)
  leave bytes empty and set `source_url`. The `image_url` property
  returns `data:{mime};base64,…` when bytes are present, else
  `source_url` — consumers read `image_url` regardless of shape.
- Why: Refs are per-image-unique and chunk-stable. Legacy
  `MarkdownLoader` captioned HTTP images via langchain `ChatOpenAI`
  (which accepts URLs natively). The new VLM ABC takes bytes only, so a
  fetch stage has to populate them — but the parser still emits one
  `ImageBlock` per in-text image, keeping the contract uniform.
- Alternatives considered: (a) positional matching of refs to images —
  rejected as fragile; (b) embedding image bytes inside `TextBlock` —
  rejected as a heavier model change.

**9. Paginated parsers emit `list[TextBlock]` with `page_number`; in-band `[PAGE_N]` markers are gone.**
Marker and PPTX previously concatenated all page content into one
`TextBlock` with `[PAGE_N]` markers between pages. They now emit one
`TextBlock` per page with `page_number` set, matching what PyMuPDF
already does. Parsers without natural pagination
(text/html/md/docx/doc/eml/whisper/image) still emit a single
`page_number=1` block.
- Why: Pagination is metadata, not content. Leaking `[PAGE_N]` markers
  into chunk text forced every consumer to know the marker syntax;
  `TextBlock.page_number` is the canonical channel and was already
  half-used.
- Implication for chunking: the chunker must NOT scan for `[PAGE_N]`
  markers. Iterate `ProcessedDocument.text_blocks` and carry
  `block.page_number` onto every emitted chunk. Page boundaries are
  block boundaries.

**10. Client-backed parsers: generic `Client*Parser` facades; `BaseOpenAIPdfClient` is scaffolding only.**
- Renamed `OpenAIPdfParser` → `ClientPdfParser`
  (`core/indexing/parsers/pdf/openai.py` → `pdf/client_based.py`); added
  `ClientAudioParser` at `core/indexing/parsers/audio/client_based.py`.
  Both accept any `BaseClientParser` and delegate `parse()`. "OpenAI"
  was a leaky model-specific label on a class that takes any
  HTTP-client-backed parser; whatever DotsOCR / Whisper-vLLM /
  Scaleway-Speech is called next quarter, the facade stays the same —
  what varies is the injected `BaseClientParser`.
- `BaseOpenAIPdfClient` provides reusable helpers (PDF page rendering,
  semaphore-protected `_ocr_one(page_img, prompt) → str | None`,
  JSON-fence stripping, JSON loading, picture-bbox cropping). It does
  **NOT** define `parse()`, a `PROMPT` class attribute, or abstract
  `_caption_images` / `_result_to_md` / `_parse_ocr_response` hooks.
  The file was renamed `_openai.py` → `_base_openai_parser.py` to
  match the new role.
- Why: The previous abstract pipeline imposed assumptions ("there's one
  OCR response per page", "captioning is a parser concern") that didn't
  generalise. Treat the base as a toolbox; let each concrete client
  (DotsOCR, future variants) drive its own `parse()` and block-emission
  strategy.
- Trade-off: more code per concrete subclass. Accepted —
  model-specific variation (response schema, block layout, bbox
  handling) lives in the subclass anyway.
- Alternative considered: keep one model-specific facade per backend.
  Rejected — duplicates the same isinstance + delegate boilerplate.

**11. DotsOCR response is validated through Pydantic.**
`DotsOCRElement` / `DotsOCRPage(RootModel[list[DotsOCRElement]])` /
`DotsOCRCategory` (Enum) capture the layout-element shape;
`DotsOCRPdfClient._parse_page` runs `model_validate` and returns `None`
on bad payloads. The `{"items": [...]}` envelope is tolerated alongside
a bare list.
- Why: Replaces dict shuffling (`page_res.get("category") == "Picture"`,
  `item.get("bbox")`) with typed access (`element.category is
  DotsOCRCategory.PICTURE`, `element.bbox`). Bad payloads fail loudly
  via `ValidationError` instead of silently returning empty markdown.

**12. `OpenAIAudioClient` keeps language detection as an injected callable, not a Ray ref-getter.**
Legacy `AudioTranscriber` looked up a `WhisperActor` Ray actor by name.
The new `OpenAIAudioClient` takes `language_detector: Callable[[Path],
Awaitable[str | None]] | None` in its constructor and skips detection
when `None` (vLLM auto-detects).
- Why: Keep the client free of Ray coupling so it can be instantiated
  and tested without a Ray cluster. The wiring layer passes a closure
  that calls the Whisper actor when `USE_WHISPER_LANG_DETECTOR=true`.
- Alternative considered: keep the Ray actor lookup inside the client
  guarded by a config flag. Rejected — pulls Ray into the
  `services/inference` layer where the rest of the file is plain HTTP.

---

## Phase 5E — Loader → Parser shims (2026-05-06)

**1. Legacy loaders are *adapter* shims, not re-export shims.**
The earlier compat-shim pass (commit `93476a6`) used pure `from X
import Y` re-exports because the symbols moved unchanged
(`ray_utils`, `text_sanitizer`, exceptions). The loader→parser move
can't do that: `BaseLoader.aload_document(file_path) → langchain
Document` and `DocumentParser.parse(document) → ProcessedDocument`
have different names *and* different contracts. Each legacy loader
becomes a `BaseLoader` adapter that reads the file into bytes, builds
a `CoreDocument`, calls the new parser, and maps `ProcessedDocument`
back to a langchain `Document`.
- Why: Preserves dynamic loader-discovery
  (`BaseLoader.__subclasses__()` in `loaders/__init__.py`) and the
  config-string lookup (`file_loaders.pdf: "MarkerLoader"`) without
  forcing every consumer to migrate at once.
- Alternative considered: pure re-exports aliasing `*Parser` as
  `*Loader`. Rejected — the discovery walk only finds `BaseLoader`
  subclasses, so an aliased `DocumentParser` would silently disappear
  from the loader registry.

**2. Shimmed in this pass: text/markdown, image, docx, doc, pptx, pymupdf, marker, local-whisper, openai-audio.**
Each adapter delegates to its core parser and, when the parser emits
`ImageBlock`s with `markdown_ref` set, layers VLM captioning on top
via the existing `BaseLoader` mixin (`self.image_captioning`,
`self.caption_images`, `self.replace_markdown_images_with_captions`).
- Why: Keeps the legacy contract intact (captioned markdown in
  `page_content`) while the canonical home is the parser. The
  `markdown_ref` substitution path is the same one the future
  caption-stage will use.

**3. `base.py` Stage 1: re-export the four image_preprocessor symbols already in core, leave the captioning mixin in place.**
`ensure_png_compatible_mode`, `HTTP_IMAGE_PATTERN`,
`DATA_URI_IMAGE_PATTERN`, `MIN_IMAGE_PIXELS` now point at the
canonical `core.indexing.image_preprocessor` symbols (class attrs
hold module-level references for `self.X` access).
`_pil_image_to_base64` rewritten on top of `pil_to_png_bytes`. The
VLM endpoint setup, `get_image_description`, `caption_images`,
`replace_markdown_images_with_captions` stay in `base.py` for now.
- Why: Mechanical, behavior-identical change. Stage 2 (move VLM
  captioning to `services/inference/captioning`) needs a design call
  (where it lives, how the shim acquires it) and is deferred.

**4. `PyMuPDFParser`: single dedicated thread + retain empty pages for 1-to-1 pagination.**
- PyMuPDF/pymupdf4llm are not thread-safe; concurrent calls raise
  `ValueError: not a textpage of this page`. Upstream maintainer
  (`pymupdf/PyMuPDF#3771`, closed wontfix) confirms this is documented
  behaviour, not a bug. The parser now uses a module-level
  `ThreadPoolExecutor(max_workers=1)` instead of `asyncio.to_thread`;
  concurrent `parse()` calls queue on the executor, eliminating the
  race against the default thread pool. The rest of the indexing
  pipeline still parallelizes — only the pymupdf step is serialized.
- Empty pages now produce a `TextBlock` with empty `text` (was
  previously dropped while keeping `page_count` accurate). Reverted so
  every page produces a `TextBlock`, keeping a 1-to-1 mapping with the
  source PDF's pagination — the legacy `\n[PAGE_N]\n` anchor format
  the loader-shim emits aligns exactly with the source.

**5. `TranscriberConfig.direct_upload_suffixes` got lost in the core/config migration; ported to `core/config/indexation.py`.**
The legacy `config/models.py:TranscriberConfig` had the field +
`|`-separated string validator + a default frozenset of audio
extensions. The active `core/config/indexation.py:TranscriberConfig`
(loaded via `openrag.core.config.loader.load_config`) was missing it,
producing `AttributeError: 'TranscriberConfig' object has no attribute
'direct_upload_suffixes'` when the audio shim accessed it.
- Why: `config/models.py` is now vestigial — kept for legacy imports
  but no longer drives `load_config()`. Fields added there but not
  mirrored to `core/config` are silently inactive at runtime.

**6. Skipped: eml, `pdf_loaders/openai.py`, `pdf_loaders/dotsocr.py`.**
- `eml_loader.py`: the new `EmlParser` takes `attachment_parsers:
  Mapping[str, DocumentParser]`, but the old loader dispatches
  attachments through `BaseLoader`-keyed `get_loader_classes` with a
  multi-tier PDF fallback chain (`MarkerLoader` → `PyMuPDFLoader` →
  `PyMuPDF4LLMLoader` → `DoclingLoader`). The contract bridge isn't
  trivial; deferred until services-side attachment-parser composition
  lands.
- `pdf_loaders/openai.py` + `pdf_loaders/dotsocr.py`: services-side
  `BaseOpenAIPdfClient` / `DotsOCRPdfClient` exist but require a
  concrete `core.vlm.VLM` to instantiate, and `vlm_registry` is empty
  (no concrete VLM impl exists yet). Both legacy classes are also dead
  code on this branch — not in any Hydra config, no external imports.
- Why: Both gaps need new services-side work (attachment-parser DI,
  `LangchainOpenAIVLM`-style concrete) before a meaningful shim is
  possible. Re-export-only "shims" would relocate the file without
  going through the new architecture, defeating the purpose.

**7. Stale files flagged for deletion (Phase 12 cleanup).**
- `components/indexer/loaders/CustomHTMLLoader.py` and
  `components/indexer/loaders/CustomDocLoader.py` — legacy
  `BaseLoader` subclasses, not referenced by any Hydra config or
  external import. Discoverable via `BaseLoader.__subclasses__()` but
  never instantiated. `CustomDocLoader` uses
  `UnstructuredWordDocumentLoader` / `UnstructuredODTLoader` — no
  clean parser equivalent in core (`DocxParser` uses MarkItDown).
- `config/models.py` (the whole file, incl. its `TranscriberConfig`)
  — superseded by `core/config/*`; kept only so legacy imports don't
  break. Drift between the two has already caused one runtime bug
  (entry 5).
- Why: Out of scope for the loader-shim pass; flagged here so they
  don't get re-shimmed by future passes. Removal coordinates with
  Phase 12 ("delete old re-export shims").

---

## Phase 6B — vLLM inference clients + legacy shims (2026-05-07)

**1. `VLLMVision(VLLMClient, VLM)` — multiple inheritance kept for nominal typing.**
`VLLMClient` provides the full implementation (httpx pool, retry,
circuit breaker, `aclose()`). `VLM` is a pure abstract mixin with no
conflicting methods, so the MRO is linear and clean. Adds only
`_max_tokens`, `caption_image()`, and `caption_images_batch()`.
- Why: VLM and LLM talk to the same vLLM OpenAI-compatible
  chat/completions endpoint, so `VLLMClient` is the right concrete
  base. Keeping `VLM` in the bases preserves nominal typing —
  `isinstance(vision, VLM)` works, and any future code that type-checks
  against the VLM ABC will accept `VLLMVision` without a cast.
- Alternative considered: single inheritance `VLLMVision(VLLMClient)`
  only, relying on structural/duck typing for registry lookup. Rejected
  — the registry is currently structurally typed, but explicit ABC
  conformance is cheap here (no diamond, no conflicting methods) and
  makes the intent clear to readers.

**2. `LLM.generate()` and `LLM.chat()` return `dict` (full OpenAI-compatible response body), not `str`.**
The original ABC typed both methods as `→ str`, which forced callers to
re-construct the surrounding OpenAI envelope when building RAG answers
(losing `model`, `usage`, `finish_reason`, etc.). The concrete vLLM
implementation already returned the full `httpx` JSON body; the `str`
annotation was aspirational, not real.
- Why: RAG answers are ultimately forwarded to the client in OpenAI format.
  Stripping to plain text at the LLM boundary means the pipeline has to
  re-wrap the content into `{"choices": [{"message": {"content": …}}]}`
  further up — metadata (token counts, model id, stop reason) is lost in
  the process. Returning `dict` preserves the full payload and keeps
  back-ends interchangeable without wrapping shims. Using bare `dict` (not
  a `TypedDict`) is a deliberate first step: it is backward-compatible with
  all current callers and eases compat-shim re-exports while the
  refactoring is still ongoing.
- Alternative considered: introduce typed response models (`ChatCompletion`,
  `CompletionResponse`, `ChatCompletionChunk`) immediately. Rejected as
  premature — Phase 6 adds the concrete client; Phase 10 (API layer
  clean-up) is the right time to freeze the contract with typed models.
  The `dict` annotation signals intent without coupling every caller to a
  model definition that will evolve.
- `stream_chat` stays `AsyncIterator[str]` yielding raw SSE lines
  (`data: {…}` strings). Parsing SSE chunks into typed dicts is Phase 10+
  work; the current shape keeps the streaming path consistent with
  OpenAI's SDK behaviour.
- Future normalisation — when the typed models land, callers will migrate
  to this pattern (TypedDict shown; Pydantic models are equally valid and
  would expose `chat_content` / `completion_text` as properties instead):

```python
from typing import TypedDict

class _Message(TypedDict):
    role: str
    content: str

class _Choice(TypedDict):
    index: int
    message: _Message        # chat completions
    finish_reason: str | None

class _CompletionChoice(TypedDict):
    index: int
    text: str                # text completions
    finish_reason: str | None

class _Usage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatCompletion(TypedDict):
    id: str
    object: str              # "chat.completion"
    model: str
    choices: list[_Choice]
    usage: _Usage

class Completion(TypedDict):
    id: str
    object: str              # "text_completion"
    model: str
    choices: list[_CompletionChoice]
    usage: _Usage

# Convenience extractors at the pipeline boundary:
def chat_content(resp: ChatCompletion) -> str:
    return resp["choices"][0]["message"]["content"]

def completion_text(resp: Completion) -> str:
    return resp["choices"][0]["text"]
```

  Until then, callers that need the text can use
  `resp["choices"][0]["message"]["content"]` directly.

---

## Phase 7A.1 — Connection manager + schema (2026-05-12)

**1. Pulled the 7A.4 migration directory move forward into the 7A.1 commit set.**
The spec files the directory move (`scripts/migrations/alembic/` →
`services/persistence/migrations/`) and the env.py rewire under a separate
subsection (**7A.4 — Migrations**), distinct from 7A.1 (`connection.py` +
`schema.py`). The move was done in this commit set anyway.
- Why: The 7A.1 work creates `schema.py` whose entire purpose is to be
  Alembic's metadata target. Leaving env.py pointing at the legacy
  `components.indexer.vectordb.models.Base.metadata` would have created a
  short-lived intermediate state where the two metadata definitions had to
  stay byte-for-byte identical (or Alembic autogenerate would flag the
  schema as drifted). Moving env.py at the same time avoids that risk
  window — once schema.py exists, env.py points at it directly.
- Alternative considered: strict 7A.1-only — create `schema.py` but leave
  the old `scripts/migrations/alembic/env.py` importing `Base.metadata`
  until 7A.4. Rejected for the dual-source-of-truth risk above, and
  because the migration move is a pure `git mv` with no code changes
  beyond two import lines.

**2. Extended `RDBConfig` with `database`, `pool_min_size`, `pool_max_size`, `command_timeout`.**
The spec's `ConnectionManager.__init__` pseudocode reads `config.database`,
`config.pool_min_size`, `config.pool_max_size` directly. None of those
fields existed on OpenRAG's `RDBConfig`. Added them (with defaults
`pool_min_size=5`, `pool_max_size=20`, `command_timeout=30`, `database=None`)
plus matching `POSTGRES_DATABASE` / `POSTGRES_POOL_{MIN,MAX}_SIZE` /
`POSTGRES_COMMAND_TIMEOUT` env-var mappings in `core/config/loader.py`.
- Why: 7A.1 doesn't compile otherwise. The spec's "files to create" table
  lists only `connection.py` + `schema.py`, but the implementation it shows
  has a hard config-shape dependency. Treated as required scaffolding for
  7A.1 rather than as a 7E (DI) concern, since the new fields belong on
  the same config object that already carries `host`/`port`/`user`/`password`.
- Alternative considered: pass DSN + pool sizes as bare positional args
  to `ConnectionManager.__init__`, leaving `RDBConfig` untouched. Rejected
  — the spec's reference implementation accepts a `PostgresConfig` object
  and the 7E DI wiring (`create_catalog_store(config)`) hands the whole
  config in. Splitting fields across the call site and the config would
  diverge from that contract.

**3. `RDBConfig.database` stays optional; `ConnectionManager.__init__` raises if it's still `None`.**
The legacy code derives the Postgres database name from the Milvus
collection name (`f"partitions_for_collection_{collection_name}"`) at
`MilvusDB` actor startup. The new `RDBConfig` could either (a) require the
caller to set `database` explicitly, or (b) compute the name itself from
`VectorDBConfig.collection_name`. Chose (a) with a None default and a
constructor-time guard.
- Why: Crossing config sections (RDB reading VectorDB) would entangle two
  otherwise independent config blocks and make `RDBConfig` non-portable.
  The collection→database mapping is an integration concern that belongs
  in the 7E DI wiring (`create_catalog_store` will build the database
  name from `config.vectordb.collection_name` and inject it). The guard
  in `ConnectionManager` makes the missing-database case fail loudly at
  construction instead of silently producing a malformed DSN.
- Alternative considered: derive the database name inside
  `RDBConfig.model_post_init` from a separately-injected collection name.
  Rejected — adds two-way coupling between config sections for no gain;
  7E handles the wiring cleanly in one place.

**4. Programmatic schema-vs-ORM diff used as the acceptance check.**
After rewriting all 7 tables as `sa.Table(...)` on a shared `MetaData`,
ran a column-by-column / index-by-index / constraint-by-constraint diff
against `components.indexer.vectordb.models.Base.metadata`. Empty diff =
passes. No assertion is shipped — this was a one-time verification, not
runtime behaviour.
- Why: Alembic autogenerate will treat any divergence between the new
  metadata target and the live database (which was built from the legacy
  ORM) as a pending schema change. The diff confirms that won't happen
  on first run, and documents the methodology for the next migration:
  any future schema change must update both `schema.py` and the legacy
  `models.py` until Phase 9 deletes the latter.
- Alternative considered: ship the diff as a runtime test in 7F.
  Deferred — 7F's repo tests already need a live Postgres; a metadata
  diff doesn't need one and can live as a one-off check until the
  legacy `models.py` goes away in Phase 9.

---

## Template for future entries

```
## Phase N — [short title] ([YYYY-MM-DD])

**K. [decision in one line].**
- Why: [what forced the call, what the docs didn't cover].
- Alternative considered: [what else was on the table, why it was rejected].
```
