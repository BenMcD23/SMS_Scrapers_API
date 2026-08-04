"""Self-check for session plan visibility and comments.

Run: PYTHONPATH=app:. python -m routers.test_session_plans

Covers the two rules that aren't obvious from reading a single endpoint:
drafts are private but submitted plans are readable by every NCO, and NCOs can
comment on plans they can't approve.
"""
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import SessionPlan
import routers.session_plans as sp
from core.db import get_or_create_user
from fastapi import HTTPException


def _idinfo(name: str) -> dict:
    return {"sub": name, "email": f"{name}@x", "given_name": name, "family_name": "T"}


def test():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    # No email or Google group lookups in a self-check.
    sp.send_email = lambda *a, **k: None
    staff_emails = {"staff@x"}
    sp._is_staff = lambda idinfo: idinfo.get("email") in staff_emails

    alice, bob, staff = (_idinfo("alice"), _idinfo("bob"), _idinfo("staff"))
    for who in (alice, bob, staff):
        get_or_create_user(db, who)

    run = asyncio.run
    plan = run(sp.create_plan(sp.SessionPlanBody(session_name="Drill", aim="a",
                                                 situation="s", mission="m", execution="e"),
                              db, alice))
    plan_id = plan["id"]

    # A draft is private to its author.
    assert run(sp.get_plan(plan_id, db, alice))["id"] == plan_id
    for who in (bob, staff):
        try:
            run(sp.get_plan(plan_id, db, who))
            raise AssertionError("draft leaked")
        except HTTPException as e:
            assert e.status_code == 404

    # Once submitted, every NCO can read it — but only staff can review it.
    run(sp.submit_plan(plan_id, db, alice))
    assert run(sp.get_plan(plan_id, db, bob))["can_review"] is False
    assert run(sp.get_plan(plan_id, db, staff))["can_review"] is True
    assert run(sp.get_plan(plan_id, db, bob))["can_edit"] is False
    # ...and it shows up in their list.
    assert plan_id in [p["id"] for p in run(sp.list_plans(db, bob))["plans"]]

    # An NCO can comment on someone else's plan, and delete only their own.
    detail = run(sp.add_comment(plan_id, sp.CommentBody(body="Nice one"), db, bob))
    assert [c["body"] for c in detail["comments"]] == ["Nice one"]
    comment_id = detail["comments"][0]["id"]
    assert run(sp.get_plan(plan_id, db, alice))["comments"][0]["can_delete"] is False
    try:
        run(sp.delete_comment(plan_id, comment_id, db, alice))
        raise AssertionError("deleted someone else's comment")
    except HTTPException as e:
        assert e.status_code == 403
    # Staff can moderate anyone's.
    assert run(sp.delete_comment(plan_id, comment_id, db, staff))["comments"] == []

    # Empty comments are rejected rather than stored.
    try:
        run(sp.add_comment(plan_id, sp.CommentBody(body="   "), db, bob))
        raise AssertionError("blank comment stored")
    except HTTPException as e:
        assert e.status_code == 400

    # Approving is unchanged, and doesn't lose the plan's comments.
    run(sp.add_comment(plan_id, sp.CommentBody(body="second"), db, alice))
    approved = run(sp.approve_plan(plan_id, sp.ReviewBody(note="Good"), db, staff))
    assert approved["status"] == "approved"
    assert len(approved["comments"]) == 1
    assert db.query(SessionPlan).count() == 1

    print("session plan visibility/comments self-check passed")


if __name__ == "__main__":
    test()
