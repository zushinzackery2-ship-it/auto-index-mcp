"""Bounded report pages, including lossless JSON fragments for large records."""
import json

PAGE_BYTES = 64 * 1024
PAGE_ITEMS = 50
FRAGMENT_FORMAT = "auto_index_report_fragment_v1"
COLLECTIONS = ("results", "blockers", "participants")


def _encoded(value):
    return json.dumps(value, ensure_ascii=True)


def _fragments(row, collection, index, budget):
    source = _encoded(row)
    offset = 0
    while offset < len(source):
        fragment = dict(format=FRAGMENT_FORMAT, collection=collection, row_index=index,
                        encoding="json", offset=offset, total_characters=len(source), data="")
        low, high = 1, min(budget, len(source) - offset)
        while low <= high:
            middle = (low + high) // 2
            fragment["data"] = source[offset:offset + middle]
            if len(_encoded(fragment)) <= budget:
                low = middle + 1
            else:
                high = middle - 1
        if high < 1:
            raise ValueError("report metadata exceeds the resource byte budget")
        fragment["data"] = source[offset:offset + high]
        yield fragment
        offset += high


def _records(result, summary, split_summary, budget):
    if split_summary:
        yield from _fragments(summary, "summary", 0, budget)
    for collection in COLLECTIONS:
        for index, row in enumerate(result.get(collection, [])):
            if len(_encoded(row)) <= budget:
                yield row
            else:
                yield from _fragments(row, collection, index, budget)


def report_page(result, operation_id, page):
    summary = dict((key, value) for key, value in result.items() if key not in COLLECTIONS)
    split_summary = len(_encoded(summary)) > PAGE_BYTES // 2
    base = dict(format="auto_index_purge_v1", operation_id=operation_id) if split_summary else summary
    # A fixed reserve keeps page boundaries identical regardless of the
    # requested page number and includes the items/page/next_uri envelope.
    budget = PAGE_BYTES - len(_encoded(base)) - 512
    number, used, output = 0, 0, []
    for row in _records(result, summary, split_summary, budget):
        size = len(_encoded(row)) + (2 if output else 0)
        if output and (len(output) == PAGE_ITEMS or used + size > budget):
            if number == page:
                return dict(base, items=output, page=page, next_uri=f"purge://{operation_id}/{page + 1}")
            number, used, output = number + 1, 0, []
            size = len(_encoded(row))
        used += size
        output.append(row)
    return dict(base, items=output if number == page else [], page=page, next_uri=None)
