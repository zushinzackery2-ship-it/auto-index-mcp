from __future__ import annotations

from pathlib import Path
import threading
import hashlib
import os
from typing import Any

# Real ONNX embedding backend.
#
# Dependencies (onnxruntime, tokenizers, numpy) are imported lazily so the core
# index/server stays importable without them. They live in the optional
# ``semantic`` extra. A missing/invalid model surfaces as None from the factory
# rather than a hard crash at import time.

# Long symbol texts are split into overlapping token windows so content beyond
# the truncation budget stays searchable instead of being silently dropped.
# The overlap keeps statements cut at a window edge visible in the next one.
WINDOW_OVERLAP_TOKENS = 24
# Bounds vector growth for pathological inputs (giant generated functions).
MAX_WINDOWS_PER_TEXT = 8


class OnnxEmbedder:
    """Sentence embedder backed by an ONNX encoder + HuggingFace fast tokenizer.

    Expected model directory layout::

        <model_dir>/model.onnx      # transformer encoder (mean-pooling output)
        <model_dir>/tokenizer.json  # HF fast tokenizer
        <model_dir>/config.json     # optional metadata (dim auto-detected)

    Produces L2-normalized vectors. Default target is MiniLM-L6-v2 (dim=384).
    """

    def __init__(
        self,
        model_dir: Path,
        max_length: int = 192,
        intra_op_num_threads: int = 1,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.max_length = max_length
        self.intra_op_num_threads = max(1, int(intra_op_num_threads))
        self._session: Any = None
        self._tokenizer: Any = None
        self._measure_tokenizer: Any = None
        self._dim: int = 0
        self._name: str = self.model_dir.name or "onnx-embedder"
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        identity = [(str((self.model_dir / name).resolve()), (self.model_dir / name).stat().st_size,
                     (self.model_dir / name).stat().st_mtime_ns) for name in ("model.onnx", "tokenizer.json")]
        self._identity = hashlib.sha256(repr(identity).encode()).hexdigest()[:16]

    def _ensure_loaded(self) -> None:
        with self._load_lock:
            if self._session is None:
                self._lazy_load()

    def _lazy_load(self) -> None:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
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
        # Pad to the longest sequence in each batch, not to max_length: mean
        # pooling masks pad tokens out, so vectors are identical either way and
        # short batches skip the wasted compute of fixed-length padding.
        self._tokenizer.enable_padding()
        # Separate instance for measuring/window-slicing: bundled tokenizer
        # files can persist their own truncation/padding config, which would
        # silently cap the token counts this backend windows on.
        self._measure_tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._measure_tokenizer.no_truncation()
        self._measure_tokenizer.no_padding()
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = self.intra_op_num_threads
        sess_options.inter_op_num_threads = 1
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess_options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        sess_options.enable_cpu_mem_arena = False
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            str(model_file),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        outputs = session.get_outputs()
        shape = outputs[0].shape if outputs else []
        dim = int(shape[-1]) if len(shape) >= 2 and isinstance(shape[-1], int) else 0
        if dim <= 0:
            raise ValueError("failed to detect embedding dimension from ONNX model")
        self._dim = dim
        self._session = session

    @property
    def dim(self) -> int:
        self._ensure_loaded()
        return self._dim

    @property
    def name(self) -> str:
        return self._name

    @property
    def space_fingerprint(self) -> str:
        # The model file identifies its output dimensions without loading ONNX.
        return f"onnx={self._identity}"

    @property
    def text_fingerprint(self) -> str:
        """Identity of the text->vector mapping beyond the model name.

        Stored vectors are only reusable when the truncation window AND the
        windowing scheme that produced them match; mixing either would
        silently blend incompatible similarity spaces. The indexer folds this
        value into the storage key so a change invalidates every stored
        vector and triggers a clean re-embed.
        """
        return (
            f"maxlen={self.max_length};"
            f"win={WINDOW_OVERLAP_TOKENS}x{MAX_WINDOWS_PER_TEXT};model={self._identity}"
        )

    def window_texts(self, text: str) -> list[str]:
        """Split ``text`` into overlapping windows that each fit the budget.

        Window one naturally carries the symbol head; later windows represent
        body middle/tail content that truncation used to drop. Name context
        for those windows is recovered at rank time by the lexical blend, so
        the head is not duplicated into every window.
        """
        self._ensure_loaded()
        encoded = self._measure_tokenizer.encode(text, add_special_tokens=False)
        ids = encoded.ids
        # encode() at embed time re-adds [CLS]/[SEP] inside max_length.
        budget = max(1, self.max_length - 2)
        if len(ids) <= budget:
            return [text]
        stride = max(1, budget - WINDOW_OVERLAP_TOKENS)
        windows: list[str] = []
        start = 0
        while start < len(ids) and len(windows) < MAX_WINDOWS_PER_TEXT:
            piece = ids[start:start + budget]
            if windows and len(piece) <= WINDOW_OVERLAP_TOKENS:
                # The tail is already fully covered by the previous overlap.
                break
            decoded = self._measure_tokenizer.decode(piece)
            if decoded.strip():
                windows.append(decoded)
            start += stride
        # A window list must never be empty: an empty list would silently
        # drop the symbol from the vector store entirely.
        return windows or [text]

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._inference_lock:
            return self._embed(texts)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        if not texts:
            return []
        self._ensure_loaded()
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
