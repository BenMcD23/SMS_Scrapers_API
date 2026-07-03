"""Self-check for the Workspace-domain gate in verify_token.

Run: PYTHONPATH=app venv/bin/python app/test_verify_domain.py
Mocks Google's token verification so no network / real token is needed.
"""

from fastapi import HTTPException

import core.security as security
from core.config import GOOGLE_DOMAIN

OUTSIDE = "someone.else.com"
assert OUTSIDE != GOOGLE_DOMAIN


def _run(idinfo):
    # Patch the Google call to return our controlled claims, unique token each
    # time so the in-process token cache never masks a case.
    security.id_token.verify_oauth2_token = lambda *a, **k: idinfo
    token = f"Bearer tok-{id(idinfo)}"
    return security.verify_token(token)


def _rejected(idinfo):
    try:
        _run(idinfo)
        return False
    except HTTPException as e:
        return e.status_code in (401, 403)


base = {"email_verified": True, "exp": 9999999999, "sub": "x"}

# in-domain via hd claim → accepted
assert _run({**base, "email": f"a@{GOOGLE_DOMAIN}", "hd": GOOGLE_DOMAIN})
# in-domain via email suffix, no hd → accepted
assert _run({**base, "email": f"b@{GOOGLE_DOMAIN}"})
# outside domain via hd → rejected
assert _rejected({**base, "email": f"c@{GOOGLE_DOMAIN}", "hd": OUTSIDE})
# outside domain via email, no hd → rejected
assert _rejected({**base, "email": f"d@{OUTSIDE}"})
# spoofed subdomain suffix must not slip through
assert _rejected({**base, "email": f"e@evil-{GOOGLE_DOMAIN}"})
# unverified email → rejected even if in-domain
assert _rejected({"email_verified": False, "exp": 9999999999,
                  "email": f"f@{GOOGLE_DOMAIN}", "hd": GOOGLE_DOMAIN})

print("ok")
