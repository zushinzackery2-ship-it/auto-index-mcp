from __future__ import annotations

from pathlib import Path
from typing import Any

# Real ONNX embedding backend.
#
# Dependencies (onnxruntime, tokenizers, numpy) are imported lazily so the core
# index/server stays importable without them. They live in the optional
# ``semantic`` extra. A missing/invalid model surfaces as None from the factory
# rather than a hard crash at import time.


class OnnxEmbedder:
    """Sentence embedder backed by an ONNX encoder + HuggingFace fast tokenizer.

    Expected model directory layout::

        <model_dir>/model.onnx      # transformer encoder (mean-pooling output)
        <model_dir>/tokenizer.json  # HF fast tokenizer
        <model_dir>/config.json     # optional metadata (dim auto-detected)

    Produces L2-normalized vectors. Default target is MiniLM-L6-v2 (dim=384).
    """

    def __init__(self, model_dir: Path, max_length: int = 256) -> None:
        self.model_dir = Path(model_dir)
        self.max_length = max_length
        self._session: Any = None
        self._tokenizer: Any = None
        self._dim: int = 0
        self._name: str = self.model_dir.name or "onnx-embedder"
        self._lazy_load()

    def _lazy_load(self) -> None:
        import onnxruntime as ort

        model_file = self.model_dir / "model.onnx"
        tokenizer_file = self.model_dir / "tokenizer.json"
        if not model_file.exists() or not tokenizer_file.exists():
            raise FileNotFoundError(
                f"ONNX embedding model requires model.onnx and tokenizer.json in {self.model_dir}"
            )
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self._tokenizer.enable_padding(length=self.max_length)
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = 1
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(model_file),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        self._dim = self._detect_dim()
        if self._dim <= 0:
            raise ValueError("failed to detect embedding dimension from ONNX model")

    def _detect_dim(self) -> int:
        outputs = self._session.get_outputs()
        if outputs:
            shape = outputs[0].shape
            if shape and len(shape) >= 2 and isinstance(shape[-1], int):
                return int(shape[-1])
        return 0

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._name

    def embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        if not texts:
            return []
        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.asarray([enc.ids for enc in encoded], dtype=np.int64)
        attention_mask = np.asarray([enc.attention_mask for enc in encoded], dtype=np.int64)
        batch, seq = input_ids.shape
        feeds: dict[str, Any] = {}
        for inp in self._session.get_inputs():
            if inp.name == "input_ids":
                feeds["input_ids"] = input_ids
            elif inp.name == "attention_mask":
                feeds["attention_mask"] = attention_mask
            elif inp.name == "token_type_ids":
                feeds["token_type_ids"] = np.zeros((batch, seq), dtype=np.int64)
        outputs = self._session.run(None, feeds)
        token_vectors = np.asarray(outputs[0], dtype=np.float32)
        mask = attention_mask.astype(np.float32)
        summed = (token_vectors * mask[:, :, None]).sum(axis=1)
        counts = mask.sum(axis=1, keepdims=True)
        counts[counts == 0] = 1.0
        mean_pooled = summed / counts
        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normalized = mean_pooled / norms
        return [row.tolist() for row in normalized]
