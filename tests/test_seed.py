import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.opportunities import models as m
from app.scripts.seed import SeedError, run_seed
from tests.helpers import auth_headers
from tests.test_eligibility_api import fill_profile

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "seed_sample.json"
B = "/api/v1"


def load() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8-sig"))


def count_opportunities(db) -> int:
    return db.scalar(select(func.count()).select_from(m.Opportunity))


def test_sample_file_seeds_cleanly_and_is_idempotent():
    data = load()
    with SessionLocal() as db:
        first = run_seed(db, data)
        second = run_seed(db, data)
        categories = dict(
            db.execute(
                select(m.Opportunity.category, func.count()).group_by(m.Opportunity.category)
            ).all()
        )
        unverified = db.scalar(
            select(func.count()).select_from(m.Opportunity).where(m.Opportunity.data_quality == "unverified")
        )
    assert first == {"organizations": 3, "sources": 3, "opportunities": 12, "skipped": 0}
    assert second == {"organizations": 0, "sources": 0, "opportunities": 0, "skipped": 12}
    assert categories == {"admission": 3, "scholarship": 3, "internship": 3, "job": 3}
    assert unverified == 12  # sample data is never verified unless asked


def test_invalid_rule_tree_aborts_before_writing_any_opportunity():
    data = load()
    data["opportunities"][1]["requirements"] = {"type": "LEAF", "field": "nope", "op": "eq", "value": 1}
    with SessionLocal() as db:
        with pytest.raises(SeedError):
            run_seed(db, data)
        assert count_opportunities(db) == 0


def test_verify_demo_only_touches_records_marked_demo():
    data = load()
    for item in data["opportunities"][1:]:
        item["demo"] = False  # simulate real data mixed in
    with SessionLocal() as db:
        run_seed(db, data, verify_demo=True)
        verified = db.scalars(
            select(m.Opportunity.title).where(m.Opportunity.data_quality == "verified")
        ).all()
    assert verified == ["BSc Computer Science (SAMPLE)"]


def test_seeded_course_evaluates_for_a_matching_student(client):
    with SessionLocal() as db:
        run_seed(db, load(), verify_demo=True)
    user = auth_headers(client, "u@example.com")
    fill_profile(client, user)  # NG, secondary, JAMB 215/400, maths A1, English B2

    items = client.get(f"{B}/opportunities", params={"q": "Computer Science"}, headers=user).json()["items"]
    assert len(items) == 1
    result = client.post(f"{B}/opportunities/{items[0]['id']}/eligibility", headers=user).json()
    assert result["status"] == "eligible"
    assert result["counts"]["satisfied"] == 4

    # a different student with an empty profile gets honesty, not a guess
    empty = auth_headers(client, "empty@example.com")
    r = client.post(f"{B}/opportunities/{items[0]['id']}/eligibility", headers=empty).json()
    assert r["status"] == "insufficient_information"