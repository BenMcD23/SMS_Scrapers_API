"""The API runs as one process, so an ``async def`` handler runs on the event
loop itself: anything blocking inside it (a query, a Gmail send, a Calendar
call, a PDF render) stalls every other request until it finishes. A plain
``def`` handler runs in FastAPI's thread pool instead. An async handler only
earns its place by awaiting something; one that never does is a blocking
handler in disguise.
"""

import ast
import inspect
import textwrap

from fastapi.routing import APIRoute

from api import app


def _routes(routes=None):
    # Included routers sit behind a wrapper in this FastAPI version; their own
    # routes are on ``original_router``.
    found = []
    for r in app.routes if routes is None else routes:
        if isinstance(r, APIRoute):
            found.append(r)
        elif hasattr(r, "original_router"):
            found += _routes(r.original_router.routes)
    return found


def _awaits(fn) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in ast.walk(tree))


def test_the_route_list_is_not_empty():
    # Guards the test below against passing vacuously if routes move.
    assert len(_routes()) > 100


def test_every_async_handler_awaits_something():
    offenders = sorted(
        f"{r.endpoint.__module__}.{r.endpoint.__name__} ({', '.join(sorted(r.methods))} {r.path})"
        for r in _routes()
        if inspect.iscoroutinefunction(r.endpoint) and not _awaits(r.endpoint)
    )
    assert offenders == [], (
        "These async handlers never await, so their blocking work runs on the event "
        "loop and stalls the whole API. Make them plain `def`:\n" + "\n".join(offenders)
    )
