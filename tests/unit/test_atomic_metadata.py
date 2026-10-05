"""Metadata readers retain snapshots and finish before atomic publication."""
import json
import multiprocessing
import os
import threading

from concurrent.futures import ThreadPoolExecutor

from auto_index_mcp.runtime.files.json import atomic_json, read_json


def test_publication_preserves_previously_read_version(tmp_path):
    path = tmp_path / "state-\u732b-\U0001f431.json"
    atomic_json(path, dict(version=1))
    previous = read_json(path)
    atomic_json(path, dict(version=2))
    assert previous == dict(version=1)
    assert read_json(path) == dict(version=2)


def test_publication_waits_for_open_metadata_reader(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    atomic_json(path, dict(version=1))
    reading, finish_read, publishing, published = [threading.Event() for _ in range(4)]
    original_load = json.load

    def paused_load(stream):
        reading.set()
        assert finish_read.wait(5)
        return original_load(stream)

    def publish():
        publishing.set()
        try:
            atomic_json(path, dict(version=2))
        finally:
            published.set()

    monkeypatch.setattr(json, "load", paused_load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(read_json, path)
        assert reading.wait(5)
        writer = executor.submit(publish)
        try:
            assert publishing.wait(5)
            if os.name == "nt":
                assert not published.wait(0.1)
        finally:
            finish_read.set()
        assert reader.result(timeout=5) == dict(version=1)
        writer.result(timeout=5)
    monkeypatch.setattr(json, "load", original_load)
    assert read_json(path) == dict(version=2)


def test_concurrent_readers_observe_only_complete_published_versions(tmp_path):
    path = tmp_path / "state.json"
    atomic_json(path, dict(version=0, payload="x" * 1000))
    finished, failures = threading.Event(), []

    def read_versions():
        while not finished.is_set():
            try:
                result = read_json(path)
                assert isinstance(result["version"], int) and result["payload"] == "x" * 1000
            except Exception as error:
                failures.append(error)
                finished.set()

    readers = [threading.Thread(target=read_versions) for _ in range(3)]
    for reader in readers:
        reader.start()
    try:
        for version in range(100):
            atomic_json(path, dict(version=version, payload="x" * 1000))
    finally:
        finished.set()
        for reader in readers:
            reader.join(5)
    assert not failures, failures
    assert all(not reader.is_alive() for reader in readers)


def _read_in_process(path, ready, finished, result):
    count = 0
    try:
        ready.set()
        while not finished.is_set():
            row = read_json(path)
            assert row["payload"] == "x" * 1000
            count += 1
        result.put(dict(reads=count, error=None))
    except Exception as error:
        result.put(dict(reads=count, error=repr(error)))


def test_metadata_publication_coordinates_another_process(tmp_path):
    path = tmp_path / "state.json"
    atomic_json(path, dict(version=0, payload="x" * 1000))
    context = multiprocessing.get_context("spawn")
    ready, finished, result = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_read_in_process, args=(path, ready, finished, result))
    process.start()
    try:
        assert ready.wait(10)
        for version in range(100):
            atomic_json(path, dict(version=version, payload="x" * 1000))
        finished.set()
        process.join(10)
        assert process.exitcode == 0
        row = result.get(timeout=5)
        assert row["error"] is None and row["reads"] > 0, row
    finally:
        finished.set()
        if process.is_alive():
            process.terminate()
            process.join(5)
        result.close()
