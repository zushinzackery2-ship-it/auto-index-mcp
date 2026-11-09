"""A query pins every source publication and resolves one workspace graph."""
from contextlib import ExitStack
from dataclasses import dataclass
import json
import logging
import sqlite3
from pathlib import Path

from ..domain.errors import ServiceError
from ..domain.reference_graph import project_references
from ..storage.generation import revision
from ..storage.graph import reference_rows
from ..storage.readers import read_files
from ..storage.symbols import query_ranked, query_relaxed
from .projection import prefix_file, prefixed_symbols, prefix_file_header, prefix_search_target
from .ranking import ranked_symbol_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSnapshot:
    prefix: str
    root: str
    connection: object
    metadata: dict

    @property
    def projection(self):
        return dict(path=self.prefix, root=self.root)


class WorkspaceSnapshot:
    def __init__(self, view):
        self._resources = ExitStack()
        self.sources = []
        try:
            self._capture(view, "")
        except BaseException:
            self.close()
            raise

    def _capture(self, view, prefix):
        connection = self._resources.enter_context(view.store.read_connect())
        metadata = {row["key"]: json.loads(row["value"]) for row in connection.execute("SELECT * FROM metadata")}
        self.sources.append(SourceSnapshot(prefix, metadata.get("root", ""), connection, metadata))
        if not view.mount_child_indexes or not metadata.get("mount_child_indexes", True):
            return
        for row in connection.execute("SELECT * FROM child_indexes ORDER BY path"):
            child = dict(row)
            try:
                if Path(child["db_path"]).resolve() in view.visited_db_paths:
                    raise ValueError("cyclic child index")
                child_view = view._child_view(child)
                self._capture(child_view, "/".join(part for part in (prefix, child["path"]) if part))
            except (OSError, KeyError, ValueError, sqlite3.Error) as exc:
                logger.warning("query phase=capture-source path=%s error=%s", child["path"], exc)
                raise ServiceError("source-unavailable", f"workspace source unavailable: {child['path']}",
                                   "rebuild the workspace to reconcile child indexes", path=child["path"]) from exc

    def close(self):
        self._resources.close()

    def scope(self):
        return dict(scope="indexed-workspace", external_callers="unknown", sources=[
            dict(path=source.prefix, revision=revision(source.metadata)) for source in self.sources])

    def headers(self, *, targets=False):
        fields = "path,language,active_source" if targets else (
            "path,name,parent,extension,language,size,mtime_ns,line_count,active_source,symbol_count")
        project = prefix_search_target if targets else prefix_file_header
        rows = []
        for source in self.sources:
            items = [dict(row) for row in source.connection.execute(f"SELECT {fields} FROM files ORDER BY path")]
            rows.extend(project(source.projection, item) if source.prefix else item for item in items)
        return sorted(rows, key=lambda item: item["path"].lower())

    def query_symbols(self, text, kind, limit, offset, *, relaxed=False):
        query = query_relaxed if relaxed else query_ranked
        rows = []
        for source in self.sources:
            items = query(source.connection, text, kind, limit + offset, 0, with_callers=False)
            rows.extend(prefixed_symbols(source.projection, items) if source.prefix else items)
        rows.sort(key=ranked_symbol_key if text else lambda row: (row["file_path"].lower(), row["line"]))
        return self.attach_references(rows[offset:offset + limit])

    def attach_references(self, symbols):
        names = list(dict.fromkeys(symbol["name"] for symbol in symbols))
        definitions, edges = [], []
        for source in self.sources:
            source_definitions, source_edges = reference_rows(source.connection, names)
            for definition in source_definitions:
                definition["file_path"] = "/".join(part for part in (source.prefix, definition["file_path"]) if part)
            for edge in source_edges:
                edge["source_file"] = "/".join(part for part in (source.prefix, edge["source_file"]) if part)
            definitions.extend(source_definitions)
            edges.extend(source_edges)
        return project_references(symbols, definitions, edges)

    def files(self, path=None):
        files = []
        for source in self.sources:
            local = path
            if path is not None and source.prefix:
                if not path.startswith(source.prefix + "/"):
                    continue
                local = path[len(source.prefix) + 1:]
            query = "SELECT * FROM files" + (" WHERE path=?" if path is not None else " ORDER BY path")
            rows = source.connection.execute(query, (local,) if path is not None else ())
            items = read_files(source.connection, rows, with_callers=False)
            files.extend(prefix_file(source.projection, item) if source.prefix else item for item in items)
        symbols = [dict(symbol, file_path=item["path"]) for item in files for symbol in item["symbols"]]
        self.attach_references(symbols)
        by_key = {(symbol["file_path"], symbol["line"], symbol["name"]): symbol for symbol in symbols}
        for item in files:
            for symbol in item["symbols"]:
                enriched = dict(by_key[(item["path"], symbol["line"], symbol["name"])])
                enriched.pop("file_path")
                symbol.update(enriched)
        return sorted(files, key=lambda item: item["path"].lower())
