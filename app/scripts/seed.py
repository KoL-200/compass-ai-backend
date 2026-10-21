"""Load organizations, sources and opportunities from a JSON file.

Usage:
    python -m app.scripts.seed data/seed_sample.json
    python -m app.scripts.seed data/seed_sample.json --verify-demo   (demo records only)
"""
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.eligibility.rules import RuleTreeInvalid, validate_tree
from app.modules.opportunities import models as m
from app.modules.opportunities import service
from app.modules.opportunities.schemas import OpportunityCreate, OrganizationCreate, SourceCreate

_adapter = TypeAdapter(OpportunityCreate)


class SeedError(Exception):
    pass


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "key"}


def _get_or_create_org(db: Session, item: dict):
    data = OrganizationCreate.model_validate(_clean(item)).model_dump()
    existing = db.scalar(
        select(m.Organization).where(
            m.Organization.name == data["name"], m.Organization.country == data["country"]
        )
    )
    if existing:
        return existing, False
    return service.create_organization(db, data), True


def _get_or_create_source(db: Session, item: dict):
    data = SourceCreate.model_validate(_clean(item)).model_dump()
    existing = db.scalar(select(m.Source).where(m.Source.url == data["url"]))
    if existing:
        return existing, False
    return service.create_source(db, data), True


def run_seed(db: Session, data: dict, *, verify_demo: bool = False) -> dict:
    counts = {"organizations": 0, "sources": 0, "opportunities": 0, "skipped": 0}
    orgs, sources = {}, {}

    try:
        for item in data.get("organizations", []):
            orgs[item["key"]], created = _get_or_create_org(db, item)
            counts["organizations"] += created
        for item in data.get("sources", []):
            sources[item["key"]], created = _get_or_create_source(db, item)
            counts["sources"] += created
    except (ValidationError, KeyError) as exc:
        raise SeedError(f"Bad organization or source entry: {exc}") from exc

    # Pass 1: validate every opportunity. Nothing is written if any of them is wrong.
    prepared = []
    for item in data.get("opportunities", []):
        body = dict(item.get("body", {}))
        title = body.get("title", "<no title>")
        org = orgs.get(item.get("org"))
        if org is None:
            raise SeedError(f"{title}: unknown organization key '{item.get('org')}'")
        body["organization_id"] = org.id
        if item.get("source"):
            source = sources.get(item["source"])
            if source is None:
                raise SeedError(f"{title}: unknown source key '{item['source']}'")
            body["source_id"] = source.id
        try:
            validated = _adapter.validate_python(body)
            tree = validate_tree(item["requirements"]) if item.get("requirements") else None
        except ValidationError as exc:
            raise SeedError(f"{title}: {exc}") from exc
        except RuleTreeInvalid as exc:
            raise SeedError(f"{title}: invalid requirements: {exc.errors}") from exc
        prepared.append((item, validated, tree, org.id, title))

    # Pass 2: write.
    for item, validated, tree, org_id, title in prepared:
        exists = db.scalar(
            select(m.Opportunity.id).where(
                m.Opportunity.title == title, m.Opportunity.organization_id == org_id
            )
        )
        if exists:
            counts["skipped"] += 1
            continue
        opp = service.create_opportunity(db, validated)
        if tree:
            opp = service.set_requirements(db, opp.id, tree)
        if verify_demo and item.get("demo") and opp.source_id:
            service.verify_opportunity(db, opp.id)  # verify last: editing rules resets it
        counts["opportunities"] += 1
    return counts


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if len(paths) != 1 or flags - {"--verify-demo"}:
        print("Usage: python -m app.scripts.seed <file.json> [--verify-demo]")
        return 1
    data = json.loads(Path(paths[0]).read_text(encoding="utf-8-sig"))
    with SessionLocal() as db:
        try:
            counts = run_seed(db, data, verify_demo="--verify-demo" in flags)
        except SeedError as exc:
            print(f"Seed failed, no opportunities were written: {exc}")
            return 1
    print("Seed complete:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))