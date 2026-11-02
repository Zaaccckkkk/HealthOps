# Knowledge base (retrieval corpus) for the assistant

Status: implemented. `src/api-service/scripts/knowledge/ingest.py` chunks the sources, embeds them with
`nomic-embed-text` through Ollama and stores them in PostgreSQL with pgvector (migration
0004); the assistant's `lookup_reference` tool retrieves passages and the `reference` finding
type quotes them verbatim within each source's quotation limit. Verified locally on the Mac
(570 chunks from 12 sources), on the cloud L4 VM and with the reference evaluation set; see
`src/api-service/eval/RESULTS.md`. One command runs the whole ingest in Compose: `make rag`
(repository root; needs an Ollama on the host with `nomic-embed-text`). A committed run log and a
complete retrieval example are in `src/api-service/examples/` (`rag-ingest-run.log`,
`reference-lookup.synthetic.json`).

## What the corpus is for

The assistant answers questions about one person's own recorded data with deterministic tools.
The knowledge base adds *reference background* that the model may cite verbatim: what a metric
means (for example SDNN versus RMSSD), what public guidelines say about activity and sleep, and
what this system's own statistics and warnings mean. It never provides material for
interpreting a user's values as healthy or unhealthy; those questions stay refused.

## Layout and policy

| Path | Content | In Git |
|---|---|---|
| `knowledge/sources.yaml` | One entry per third-party source: URL, publisher, licence checked on the retrieval date, allowed use, sha256, size | yes |
| `knowledge/own/*.md` | Documents written for this project (statistical definitions, warning glossary, channel notes) | yes |
| `src/api-service/.local/knowledge/raw/` | Downloaded third-party PDFs and pages, fetched by `src/api-service/scripts/knowledge/fetch_sources.py` | no |

Third-party texts are never committed, even public-domain ones: the manifest plus the fetch
script reproduce them, and copyrighted help pages (Apple, Google) and the AASM statement are
used by short quotation with a link only. Licences recorded so far: U.S. federal works (HHS,
NHLBI, MedlinePlus; public domain text), WHO (CC BY-NC-SA 3.0 IGO), OpenStax (CC BY-NC-SA 4.0,
attribution required), Apple and Google documentation and the AASM statement (copyright,
quotation only). The non-commercial terms fit a course project; a commercial use would have to
drop the NC-licensed sources.

## Ingesting

```bash
cd src/api-service && HEALTHOPS_ASSISTANT_MODEL_BASE_URL=http://127.0.0.1:11434 uv run python scripts/knowledge/ingest.py
```

Chunks follow document structure (HTML headings, PDF pages, Markdown sections), merged or
split to roughly 400 to 1800 characters at sentence boundaries. Each chunk carries the
source's licence and a quotation limit: 1800 characters for public-domain and Creative
Commons sources, 300 for copyrighted documentation quoted under short-quotation terms. The
embedding model name is stored per chunk; switching models means re-ingesting.

## Fetching

```bash
cd src/api-service && uv run python scripts/knowledge/fetch_sources.py            # downloads, verifies sha256
cd src/api-service && uv run python scripts/knowledge/fetch_sources.py --check    # verifies existing files only
```

A changed checksum means the publisher changed the page; re-read the licence and update the
manifest deliberately rather than silently accepting the new text.
