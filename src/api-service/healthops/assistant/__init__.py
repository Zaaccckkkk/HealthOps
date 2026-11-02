"""Single-turn question answering over the deterministic history tools.

Layering: ``contracts`` (request/response), ``model`` (chat-model interface and clients),
``time_semantics`` (server-computed dates), ``toolbox`` (tool schemas, validation, bounded
execution with injected identity), ``numeric`` (numeric-consistency diagnostic) and
``orchestrator`` (the bounded tool-calling loop). No RAG, no conversation storage.
"""
