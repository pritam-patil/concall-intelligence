"""Provider interfaces for embeddings and generation.

Both the embedding model and the generation model are pinned choices (see
ARCHITECTURE.md) reached behind a small interface, not because a swap is
imminent, but because free-tier quotas can change or disappear and the
pipeline/API code should not need to change when that happens.
"""
