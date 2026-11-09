"""Workspace retirement fixtures keep distinct projects in distinct caches."""
import pytest

from auto_index_mcp.core.service import AutoIndexService


@pytest.fixture
def make_service():
    services = []

    def create(project, rebuild=True, index_root=None, mount_child_indexes=False):
        service = AutoIndexService(index_root=index_root)
        services.append(service)
        service.enable(str(project), rebuild=rebuild, refresh_embedder=False, mount_child_indexes=mount_child_indexes)
        return service

    yield create
    for service in services:
        service.disable()
