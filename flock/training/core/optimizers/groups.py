from torch import nn

# Group names follow OLMo-core's `group_overrides`, which key groups by parameter role.
EMBEDDINGS_GROUP = "embeddings"


def split_embedding_parameters(model: nn.Module) -> tuple[list[nn.Parameter], list[nn.Parameter]]:
    """Split a model's trainable parameters into embedding weights and the rest.

    Embedding weights are the parameters of `nn.Embedding` modules, including
    subclasses. A weight tied to an output projection is the same parameter, so it
    is an embedding weight and appears once. Every trainable parameter is in
    exactly one list, in `model.parameters()` order.

    Args:
        model (`torch.nn.Module`): Model whose trainable parameters to split.

    Returns:
        `tuple[list[torch.nn.Parameter], list[torch.nn.Parameter]]`: Embedding
        weights, then every other trainable parameter.
    """
    embedding_ids = {
        id(param)
        for module in model.modules()
        if isinstance(module, nn.Embedding)
        for param in module.parameters(recurse=False)
    }
    embeddings: list[nn.Parameter] = []
    others: list[nn.Parameter] = []
    # `parameters()` yields a tied parameter once, under its first name.
    for param in model.parameters():
        if param.requires_grad:
            (embeddings if id(param) in embedding_ids else others).append(param)
    return embeddings, others
