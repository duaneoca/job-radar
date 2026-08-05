"""Pulling the score object out of whatever the model actually said.

The old rule was text[find("{") : rfind("}")+1]. It broke in both directions on
a model that narrates around its answer, which is exactly what production hit:
4,928 of one user's 4,928 jobs went unscored because responses arrived as
reasoning prose with the JSON somewhere inside, or truncated mid-object.
"""

import json

import pytest

from app.reviewer import extract_json_object

GOOD = '{"score": 7.0, "skills_rank": 8, "summary": "fine", "recommended": true}'


def test_plain_json():
    assert extract_json_object(GOOD) == GOOD


def test_markdown_fence():
    assert json.loads(extract_json_object(f"```json\n{GOOD}\n```"))["score"] == 7.0


def test_reasoning_before_the_answer():
    """The real shape of the failure: the model shows its working first."""
    text = f"Average: 21 / 5 = 4.2 -> Round to 1 decimal: **4.2**\nHere it is:\n{GOOD}"
    assert json.loads(extract_json_object(text))["score"] == 7.0


def test_a_brace_in_the_prose_does_not_start_the_object_early():
    """find("{") used to grab the first brace anywhere, including one inside
    commentary, and everything after it failed to parse."""
    text = f"The rubric says {{score}} must be a float. Answer:\n{GOOD}"
    assert json.loads(extract_json_object(text))["score"] == 7.0


def test_an_example_object_before_the_real_one_loses_to_the_real_one():
    """Reasoning models sometimes restate the template before answering. The
    LAST balanced object is the answer."""
    template = '{"score": 0.0, "summary": "example"}'
    assert json.loads(extract_json_object(f"Format:\n{template}\nAnswer:\n{GOOD}"))["score"] == 7.0


def test_truncated_object_is_not_returned_as_a_fragment():
    """The token ceiling cut responses off mid-key. rfind("}") then landed on an
    earlier brace and produced a fragment that failed to parse anyway — this now
    says "nothing usable" instead, which is the honest answer."""
    assert extract_json_object('{"score": 4.0, "skills_rank": 3, "location_rank":') is None


def test_truncated_after_a_complete_object_keeps_the_complete_one():
    assert json.loads(extract_json_object(f'{GOOD}\n{{"score": 1.4,'))["score"] == 7.0


def test_braces_inside_strings_are_not_structure():
    """The naive version counted these; a summary mentioning a brace would
    unbalance the scan."""
    text = '{"summary": "they want {braces} and \\"quotes\\"", "score": 5.0}'
    assert json.loads(extract_json_object(text))["summary"] == 'they want {braces} and "quotes"'


def test_nested_objects_survive():
    text = '{"score": 5.0, "meta": {"inner": {"deep": 1}}}'
    assert json.loads(extract_json_object(text))["meta"]["inner"]["deep"] == 1


@pytest.mark.parametrize("text", ["", "no json at all", "}{", "{unclosed"])
def test_nothing_usable_returns_none(text):
    assert extract_json_object(text) is None
