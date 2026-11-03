import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.opportunities import models as m
from app.scripts.seed import SeedError, run_seed
CATALOG = Path(__file__).resolve().parent.parent / "data" / "seed_sources.json"


def load() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8-sig"))


def count_opportunities(db) -> int:
    return db.scalar(select(func.count()).select_from(m.Opportunity))


def test_source_catalog_seeds_cleanly_and_is_idempotent():
    data = load()
    assert data["organizations"] == []
    assert data["opportunities"] == []
    with SessionLocal() as db:
        first = run_seed(db, data)
        second = run_seed(db, data)
        source_count = db.scalar(select(func.count()).select_from(m.Source))
    assert first == {"organizations": 0, "sources": 40, "opportunities": 0, "skipped": 0}
    assert second == {"organizations": 0, "sources": 0, "opportunities": 0, "skipped": 0}
    assert source_count == 40


def _seed_payload(items):
    return {
        "organizations": [
            {"key": "test-org", "name": "Seed Test Organization", "type": "university", "country": "NG"}
        ],
        "sources": [
            {"key": "test-source", "name": "Seed Test Source", "url": "https://seed-test.example.org/"}
        ],
        "opportunities": [
            {
                "org": "test-org",
                "source": "test-source",
                "demo": is_demo,
                "body": {
                    "category": "admission",
                    "title": title,
                    "admission": {"degree_type": "bachelor"},
                },
            }
            for title, is_demo in items
        ],
    }


def test_invalid_rule_tree_aborts_before_writing_any_opportunity():
    data = _seed_payload([("Invalid Test Listing", False)])
    data["opportunities"][0]["requirements"] = {
        "type": "LEAF", "field": "nope", "op": "eq", "value": 1
    }
    with SessionLocal() as db:
        with pytest.raises(SeedError):
            run_seed(db, data)
        assert count_opportunities(db) == 0


def test_verify_demo_only_touches_records_marked_demo():
    data = _seed_payload([("Marked Test Listing", True), ("Unmarked Test Listing", False)])
    with SessionLocal() as db:
        run_seed(db, data, verify_demo=True)
        verified = db.scalars(
            select(m.Opportunity.title).where(m.Opportunity.data_quality == "verified")
        ).all()
    assert verified == ["Marked Test Listing"]