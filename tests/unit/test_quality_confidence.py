from auto_index_mcp.quality.dangling import dangling_report


def test_ambiguous_receivers_are_not_confidently_unused():
    files = []
    for path in ("a.py", "b.py"):
        files.append(dict(path=path, language="python", imports=[], module_refs=[], symbols=[
            dict(name="run", kind="method", line=1, calls=[], refs=[], called_by=[]),
        ]))
    files.append(dict(path="main.py", language="python", imports=[], module_refs=["run"], symbols=[]))
    assert not dangling_report(files, [], False)["findings"]
    detailed = dangling_report(files, [], True)["findings"]
    assert all(item["confidence"] == "low" for item in detailed)
