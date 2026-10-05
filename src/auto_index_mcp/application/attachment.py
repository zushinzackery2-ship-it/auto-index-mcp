"""Publish cache ownership and its mount inside the purge admission barrier."""
from ..registry.adoption import adopt_directory
from ..registry.markers import write_index_markers
from ..runtime.locking.session import SessionLease


def attach_cache(project, root, index_root):
    with project.control.store.transaction(timeout=3.0):
        project.control.check(root)
        if index_root.exists():
            adopt_directory(project.registry, index_root, root)
        write_index_markers(index_root, root)
        session = SessionLease(index_root, root=root, control=project.control)
        session.acquire()
        return session
