"""Register embedder implementations with the core registry."""


def register_embedders() -> None:
    import services.inference.vllm_client  # noqa: F401
