from __future__ import annotations

from pathlib import Path

import pytest

from auto_index_mcp.embedding.backend import resolve_embedding_max_length

onnx_deps = pytest.importorskip("onnxruntime", reason="semantic extra not installed")
pytest.importorskip("tokenizers", reason="semantic extra not installed")

from auto_index_mcp.embedding.onnx_backend import (  # noqa: E402
    MAX_WINDOWS_PER_TEXT,
    WINDOW_OVERLAP_TOKENS,
    OnnxEmbedder,
)

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "minilm-onnx"

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "model.onnx").is_file() or not (MODEL_DIR / "tokenizer.json").is_file(),
    reason="bundled minilm-onnx model not present",
)


@pytest.fixture(scope="module")
def embedder() -> OnnxEmbedder:
    return OnnxEmbedder(MODEL_DIR, max_length=resolve_embedding_max_length({}))


def _long_code(markers: int) -> str:
    lines = ["def very_long_function(argument):"]
    for index in range(markers):
        lines.append(f"    marker_{index} = compute_value_{index}(argument, {index})")
    lines.append("    return argument")
    return "\n".join(lines)


def test_short_text_is_a_single_identity_window(embedder: OnnxEmbedder) -> None:
    text = "def add(a, b):\n    return a + b"
    assert embedder.window_texts(text) == [text]


def test_long_text_produces_multiple_windows(embedder: OnnxEmbedder) -> None:
    windows = embedder.window_texts(_long_code(120))
    assert len(windows) >= 2


def test_every_window_fits_the_truncation_budget(embedder: OnnxEmbedder) -> None:
    windows = embedder.window_texts(_long_code(120))
    budget = embedder.max_length - 2
    for window in windows:
        encoded = embedder._measure_tokenizer.encode(window, add_special_tokens=False)
        # decode/re-encode may merge a boundary wordpiece; allow one token of
        # fuzz but never a window that meaningfully overflows the budget.
        assert len(encoded.ids) <= budget + 1


def test_windows_cover_head_and_tail_content(embedder: OnnxEmbedder) -> None:
    # 60 marker lines stay within the window cap, so every marker must be
    # covered. decode() renders wordpieces space-separated ("marker _ 0"),
    # hence the space-stripped comparison.
    windows = embedder.window_texts(_long_code(60))
    assert len(windows) < MAX_WINDOWS_PER_TEXT
    joined = "".join(windows).replace(" ", "")
    assert "very_long_function" in windows[0].replace(" ", "")
    assert "marker_0" in joined
    assert "marker_59" in joined


def test_window_count_is_capped(embedder: OnnxEmbedder) -> None:
    windows = embedder.window_texts(_long_code(4000))
    assert len(windows) == MAX_WINDOWS_PER_TEXT


def test_windows_embed_to_normalized_vectors(embedder: OnnxEmbedder) -> None:
    windows = embedder.window_texts(_long_code(80))
    vectors = embedder.embed(windows)
    assert len(vectors) == len(windows)
    for vector in vectors:
        assert len(vector) == embedder.dim
        norm = sum(value * value for value in vector) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-3)


def test_fingerprint_binds_window_scheme_and_maxlen(embedder: OnnxEmbedder) -> None:
    fingerprint = embedder.text_fingerprint
    assert f"maxlen={embedder.max_length}" in fingerprint
    assert f"win={WINDOW_OVERLAP_TOKENS}x{MAX_WINDOWS_PER_TEXT}" in fingerprint


def test_real_model_semantic_pipeline_with_long_symbol(
    tmp_path: Path,
    write_file,
    install_embedder,
    make_service,
    wait_embedding,
) -> None:
    project = tmp_path / "proj"
    body_lines = "\n".join(
        f"    step_{index} = normalize_batch(step_{index}, {index})" for index in range(150)
    )
    write_file(
        project / "pipeline.py",
        "def normalize_all_batches(batches):\n"
        + body_lines
        + "\n    return batches\n\n\ndef open_file(path):\n    return path\n",
    )
    install_embedder(OnnxEmbedder(MODEL_DIR, max_length=resolve_embedding_max_length({})))
    service = make_service(project)
    result = wait_embedding(service)
    # The long function must have produced several window vectors.
    assert result["embedded"] > 2

    search = service.semantic_search("normalize all batches", limit=3)
    top = search["items"][0]
    assert top["symbol_name"] == "normalize_all_batches"
    assert {"score", "vector_score", "lexical_score"} <= set(top)

    status = service.embedding_status()
    assert status["vector_count"] > status["embedded_symbol_count"]
    assert status["embedded_symbol_count"] == 2
