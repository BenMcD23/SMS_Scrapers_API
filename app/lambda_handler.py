"""AWS Lambda entrypoint — scheduled jobs first, then the HTTP app.

The same function serves two kinds of event:

* An EventBridge Scheduler rule invoking us with ``{"job": "<name>"}`` — the
  replacement for the APScheduler jobs that used to live in the app lifespan
  (see core/jobs.py). Dispatched before Mangum, because these events look
  nothing like an HTTP request.
* Everything else: a Lambda Function URL request, adapted to ASGI by Mangum.

A Function URL rather than API Gateway: no 29-second timeout ceiling (the PDF
and form generators can run long), no per-request charge, and CORS stays in
FastAPI's middleware where the anchored Vercel-preview regex already lives.
Do *not* also configure CORS on the Function URL itself — two layers means two
`Access-Control-Allow-Origin` headers, which browsers reject outright.
"""

import traceback

from mangum import Mangum

from api import app
from core.jobs import JOBS

# lifespan="off": under Lambda the app's lifespan is a deliberate no-op (the
# scheduler it used to start can't live in a frozen container), so there is
# nothing to run and every cold start saves the round trip.
_asgi_handler = Mangum(app, lifespan="off")


def handler(event, context):
    job = event.get("job") if isinstance(event, dict) else None
    if job:
        fn = JOBS.get(job)
        if fn is None:
            print(f"[lambda] unknown scheduled job {job!r}", flush=True)
            return {"ok": False, "job": job, "error": "unknown job"}
        print(f"[lambda] running scheduled job {job}", flush=True)
        try:
            fn()
        except Exception as e:
            # Raising would make EventBridge retry a job that is usually not
            # safe to retry blind (the expiry alert emails as it stamps).
            print(f"[lambda] job {job} failed:\n{traceback.format_exc()}", flush=True)
            return {"ok": False, "job": job, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "job": job}

    return _asgi_handler(event, context)
