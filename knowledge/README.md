# Knowledge base (retrieval corpus) for the assistant

Status: corpus collection only; retrieval, embeddings and the `lookup_reference` tool are
planned and not implemented. See `docs/architecture.md` for the intended design.

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
| `.local/knowledge/raw/` | Downloaded third-party PDFs and pages, fetched by `scripts/knowledge/fetch_sources.py` | no |

Third-party texts are never committed, even public-domain ones: the manifest plus the fetch
script reproduce them, and copyrighted help pages (Apple, Google) and the AASM statement are
used by short quotation with a link only. Licences recorded so far: U.S. federal works (HHS,
NHLBI, MedlinePlus; public domain text), WHO (CC BY-NC-SA 3.0 IGO), OpenStax (CC BY-NC-SA 4.0,
attribution required), Apple and Google documentation and the AASM statement (copyright,
quotation only). The non-commercial terms fit a course project; a commercial use would have to
drop the NC-licensed sources.

## Fetching

```bash
uv run python scripts/knowledge/fetch_sources.py            # downloads, verifies sha256
uv run python scripts/knowledge/fetch_sources.py --check    # verifies existing files only
```

A changed checksum means the publisher changed the page; re-read the licence and update the
manifest deliberately rather than silently accepting the new text.
