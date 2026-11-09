"""The service owns admission; adapters only schedule and translate errors."""
from contextlib import contextmanager
from functools import wraps

from ..domain.errors import ServiceError


@contextmanager
def request(project, *, activation=False, query=False, allow_suspended=False):
    with project._request_lock:
        nested = getattr(project._request_local, "active", False)
        if not nested and not activation:
            if not allow_suspended:
                project.control.check(project.root_path)
            if project.quiescing:
                raise ServiceError("busy", "project lifecycle is stopping")
        project._request_local.active = True
        try:
            if query:
                view = project.view
                project._request_local.view = view
                with view.read_scope():
                    yield
            else:
                yield
        finally:
            if not nested:
                project._request_local.active = False
                project._request_local.view = None


def operation(*, activation=False, query=False):
    def decorate(function):
        @wraps(function)
        def invoke(service, *args, **kwargs):
            with request(service.project, activation=activation, query=query):
                return function(service, *args, **kwargs)
        return invoke
    return decorate
