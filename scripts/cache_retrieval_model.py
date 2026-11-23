"""Stage the pinned retrieval-test model: python -m scripts.cache_retrieval_model."""

from pathlib import Path
import sys

from huggingface_hub import snapshot_download
from huggingface_hub.constants import HF_HUB_CACHE
from huggingface_hub.utils import LocalEntryNotFoundError


MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
# Only the PyTorch SentenceTransformer snapshot, not alternative backends.
# Use safetensors weights and require the module manifest so a partial cache
# cannot silently fall back to an auto-generated pooling pipeline.
MODEL_FILES = (
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)


class RetrievalModelCacheError(RuntimeError):
    """The pinned test model is not ready for offline use."""


def _resolve_snapshot(*, local_files_only: bool) -> Path:
    try:
        path = Path(snapshot_download(
            repo_id=MODEL_ID,
            revision=MODEL_REVISION,
            allow_patterns=MODEL_FILES,
            local_files_only=local_files_only,
            token=False,
        ))
    except LocalEntryNotFoundError as exc:
        raise RetrievalModelCacheError(
            f"Snapshot {MODEL_ID}@{MODEL_REVISION} is unavailable in {HF_HUB_CACHE}."
        ) from exc

    missing = [name for name in MODEL_FILES if not (path / name).is_file()
               or (path / name).stat().st_size == 0]
    if missing:
        raise RetrievalModelCacheError(
            f"Incomplete snapshot {MODEL_ID}@{MODEL_REVISION} in {path}; "
            f"missing or empty files: {', '.join(missing)}."
        )
    return path


def cached_retrieval_model_path() -> Path:
    """Resolve the exact cached snapshot without contacting Hugging Face."""
    return _resolve_snapshot(local_files_only=True)


def main() -> int:
    try:
        path = _resolve_snapshot(local_files_only=False)
    except Exception as exc:
        print(
            f"Retrieval model setup failed for {MODEL_ID}@{MODEL_REVISION}. "
            "Run setup on a machine that can reach Hugging Face and check that "
            f"the cache is writable ({HF_HUB_CACHE}).\n{exc}",
            file=sys.stderr,
        )
        return 1
    print(f"Cached {MODEL_ID}@{MODEL_REVISION} at {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
