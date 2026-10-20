import pytest

from app.modules.eligibility.rules import RuleTreeInvalid, validate_tree


def L(field, op, value, **extra):
    return {"type": "LEAF", "field": field, "op": op, "value": value, **extra}


def errors_for(tree):
    with pytest.raises(RuleTreeInvalid) as exc:
        validate_tree(tree)
    return exc.value.errors


def test_valid_tree_is_normalized():
    tree = validate_tree(
        {"type": "and", "children": [
            L("country", "in", [" ng "]),
            L("subjects", "contains_all", ["Mathematics ", "English"]),
        ]}
    )
    assert tree["type"] == "AND"
    assert tree["children"][0]["value"] == ["NG"]
    assert tree["children"][1]["value"] == ["mathematics", "english"]


def test_unknown_field_and_wrong_operator():
    assert "unknown field" in errors_for(L("favourite_colour", "eq", "blue"))[0]["message"]
    assert errors_for(L("country", "gte", "NG"))[0]["path"] == "$.op"


def test_required_parameters_and_extra_keys():
    assert any(e["path"] == "$.qualification_type" for e in errors_for(L("qualification_score", "gte", 200)))
    assert errors_for(L("country", "eq", "NG", surprise=1))[0]["path"] == "$.surprise"


def test_value_type_checks():
    assert errors_for(L("graduation_year", "gte", True))  # bool is not a number
    assert errors_for(L("graduation_year", "between", [2030, 2020]))
    assert errors_for(L("education_level", "gte", "other"))  # not rankable
    assert errors_for(L("date_of_birth", "before", "not-a-date"))
    assert errors_for(L("subject_grade", "gte", "z9", subject="english", grade_scale="waec"))
    assert errors_for(L("country", "in", []))


def test_size_limits():
    deep = L("country", "eq", "NG")
    for _ in range(6):
        deep = {"type": "AND", "children": [deep]}
    assert "nested too deeply" in errors_for(deep)[0]["message"]

    block = {"type": "AND", "children": [L("country", "eq", "NG")] * 20}
    wide = {"type": "AND", "children": [block, block, block]}
    assert "too many nodes" in errors_for(wide)[0]["message"]


def test_structure_errors():
    assert errors_for({"type": "AND", "children": []})
    assert errors_for("nonsense")
    assert errors_for({"type": "MAYBE"})