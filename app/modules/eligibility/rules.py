"""Rule vocabulary and tree validation. Pure Python: no database, no network."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

MAX_DEPTH = 5
MAX_NODES = 50
MAX_CHILDREN = 20
MAX_LIST = 50

# "other" exists as a declared level but has no rank, so it can't be compared with gte/lte.
LEVELS = {"secondary": 1, "diploma": 2, "bachelor": 3, "master": 4, "doctorate": 5}
ALL_LEVELS = set(LEVELS) | {"other"}

GRADE_SCALES: dict[str, list[str]] = {  # best grade first
    "waec": ["a1", "b2", "b3", "c4", "c5", "c6", "d7", "e8", "f9"],
    "alevel": ["a*", "a", "b", "c", "d", "e", "u"],
    "letter": ["a", "b", "c", "d", "e", "f"],
}


def grade_rank(scale: str, grade) -> int | None:
    """Higher rank = better grade. None if the grade isn't on that scale."""
    order = GRADE_SCALES.get(scale)
    g = str(grade).strip().lower() if grade is not None else ""
    if order is None or g not in order:
        return None
    return len(order) - order.index(g)


@dataclass(frozen=True)
class FieldSpec:
    kind: str  # text | number | level | bool | date | set | grade
    ops: frozenset
    required: tuple = ()
    optional: tuple = ()


_NUM = frozenset({"eq", "gte", "lte", "between"})
_TEXT = frozenset({"eq", "ne", "in", "not_in"})
_SET = frozenset({"contains", "contains_all", "contains_any"})

FIELDS: dict[str, FieldSpec] = {
    "country": FieldSpec("text", _TEXT),
    "nationality": FieldSpec("text", _TEXT),
    "field_of_study": FieldSpec("text", _TEXT),
    "education_level": FieldSpec("level", frozenset({"eq", "ne", "in", "not_in", "gte", "lte"})),
    "graduation_year": FieldSpec("number", _NUM),
    "age": FieldSpec("number", _NUM),
    "date_of_birth": FieldSpec("date", frozenset({"before", "after"})),
    "needs_financial_aid": FieldSpec("bool", frozenset({"eq"})),
    "experience_months": FieldSpec("number", _NUM),
    "skills": FieldSpec("set", _SET),
    "subjects": FieldSpec("set", _SET),
    "qualification_types": FieldSpec("set", _SET),
    "qualification_score": FieldSpec(
        "number", _NUM, required=("qualification_type",), optional=("scale",)
    ),
    "subject_grade": FieldSpec(
        "grade", frozenset({"eq", "gte", "lte"}), required=("subject", "grade_scale")
    ),
}


class RuleTreeInvalid(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("invalid rule tree")
        self.errors = errors


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _text(field: str, v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError("must be non-empty text")
    v = v.strip()
    return v.upper() if field in ("country", "nationality") else v.lower()


def _scalar(field: str, spec: FieldSpec, v, params: dict, op: str):
    kind = spec.kind
    if kind in ("text", "set"):
        return _text(field, v)
    if kind == "number":
        if not _is_num(v):
            raise ValueError("must be a number")
        return v
    if kind == "bool":
        if not isinstance(v, bool):
            raise ValueError("must be true or false")
        return v
    if kind == "level":
        s = _text(field, v)
        allowed = ALL_LEVELS if op in ("eq", "ne", "in", "not_in") else set(LEVELS)
        if s not in allowed:
            raise ValueError(f"must be one of {sorted(allowed)}")
        return s
    if kind == "grade":
        s = _text(field, v)
        if grade_rank(params["grade_scale"], s) is None:
            raise ValueError(f"'{v}' is not a grade on the {params['grade_scale']} scale")
        return s
    if kind == "date":
        try:
            return date.fromisoformat(str(v)).isoformat()
        except ValueError:
            raise ValueError("must be a date like 2025-01-31")
    raise ValueError("unsupported field kind")


def _value(field: str, spec: FieldSpec, op: str, value, params: dict):
    if op in ("in", "not_in", "contains_all", "contains_any"):
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_LIST:
            raise ValueError(f"must be a list of 1 to {MAX_LIST} values")
        return list(dict.fromkeys(_scalar(field, spec, v, params, op) for v in value))
    if op == "between":
        ok = isinstance(value, list) and len(value) == 2 and all(_is_num(v) for v in value)
        if not ok:
            raise ValueError("must be [min, max] as two numbers")
        if value[0] > value[1]:
            raise ValueError("min cannot be greater than max")
        return list(value)
    return _scalar(field, spec, value, params, op)


def validate_tree(raw) -> dict:
    """Validate a rule tree and return a normalized copy. Raises RuleTreeInvalid."""
    errors: list[dict] = []
    count = 0

    def err(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    def walk(node, path: str, depth: int):
        nonlocal count
        count += 1
        if count > MAX_NODES:
            if count == MAX_NODES + 1:
                err(path, f"too many nodes (max {MAX_NODES})")
            return None
        if depth > MAX_DEPTH:
            err(path, f"tree is nested too deeply (max {MAX_DEPTH} levels)")
            return None
        if not isinstance(node, dict):
            err(path, "must be an object")
            return None

        kind = str(node.get("type", "")).upper()
        if kind in ("AND", "OR"):
            return group(node, path, depth, kind)
        if kind == "LEAF":
            return leaf(node, path)
        err(f"{path}.type", "must be AND, OR or LEAF")
        return None

    def label_of(node, path: str):
        label = node.get("label")
        if label is None:
            return None
        if not isinstance(label, str) or len(label) > 200:
            err(f"{path}.label", "must be text of at most 200 characters")
            return None
        return label.strip() or None

    def group(node, path: str, depth: int, kind: str):
        for key in set(node) - {"type", "children", "label"}:
            err(f"{path}.{key}", "not allowed in a group")
        children = node.get("children")
        if not isinstance(children, list) or not 1 <= len(children) <= MAX_CHILDREN:
            err(f"{path}.children", f"must be a list of 1 to {MAX_CHILDREN} nodes")
            return None
        out = [walk(c, f"{path}.children[{i}]", depth + 1) for i, c in enumerate(children)]
        result = {"type": kind, "children": out}
        label = label_of(node, path)
        if label:
            result["label"] = label
        return result

    def leaf(node, path: str):
        field = node.get("field")
        spec = FIELDS.get(field) if isinstance(field, str) else None
        if spec is None:
            err(f"{path}.field", f"unknown field. Allowed: {', '.join(sorted(FIELDS))}")
            return None

        allowed = {"type", "field", "op", "value", "label", "needs_review"}
        allowed |= set(spec.required) | set(spec.optional)
        for key in set(node) - allowed:
            err(f"{path}.{key}", f"not allowed for field '{field}'")

        op = node.get("op")
        if op not in spec.ops:
            err(f"{path}.op", f"'{op}' is not valid for '{field}'. Allowed: {sorted(spec.ops)}")
            return None

        params: dict = {}
        for name in spec.required + spec.optional:
            if name not in node:
                if name in spec.required:
                    err(f"{path}.{name}", "is required for this field")
                continue
            raw_p = node[name]
            try:
                if name == "scale":
                    if not _is_num(raw_p) or raw_p <= 0:
                        raise ValueError("must be a positive number")
                    params[name] = raw_p
                elif name == "grade_scale":
                    s = _text(name, raw_p)
                    if s not in GRADE_SCALES:
                        raise ValueError(f"must be one of {sorted(GRADE_SCALES)}")
                    params[name] = s
                else:
                    params[name] = _text(name, raw_p)
            except ValueError as exc:
                err(f"{path}.{name}", str(exc))
        if any(n not in params for n in spec.required):
            return None

        if "value" not in node:
            err(f"{path}.value", "is required")
            return None
        try:
            value = _value(field, spec, op, node["value"], params)
        except ValueError as exc:
            err(f"{path}.value", str(exc))
            return None

        result = {"type": "LEAF", "field": field, "op": op, "value": value, **params}
        label = label_of(node, path)
        if label:
            result["label"] = label
        review = node.get("needs_review")
        if review is not None and not isinstance(review, bool):
            err(f"{path}.needs_review", "must be true or false")
        elif review:
            result["needs_review"] = True
        return result

    tree = walk(raw, "$", 1)
    if errors:
        raise RuleTreeInvalid(errors)
    return tree