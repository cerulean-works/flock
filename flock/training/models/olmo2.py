from torch import nn
from transformers import AutoTokenizer, TokenizersBackend


class Olmo2_1B(nn.Module):
    hf_key = "allenai/OLMo-2-0425-1B"

    @staticmethod
    def get_tokenizer() -> TokenizersBackend:
        tokenizer = AutoTokenizer.from_pretrained(Olmo2_1B.hf_key, padding_side="left")
        if not isinstance(tokenizer, TokenizersBackend):
            raise TypeError(
                f"Tokenizer from HF {Olmo2_1B.hf_key} did not produce a TokenizersBackend instance"
            )

        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.bos_token_id
        return tokenizer
