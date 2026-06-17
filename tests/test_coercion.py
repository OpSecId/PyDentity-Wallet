import pytest

from app.utils.coercion import as_list


def test_as_list_from_object():
    query = {"type": "DIDAuthentication", "acceptedMethods": [{"method": "key"}]}
    assert as_list(query, label="query") == [query]


def test_as_list_from_list():
    queries = [{"type": "DIDAuthentication"}, {"type": "QueryByExample"}]
    assert as_list(queries, label="query") == queries


def test_as_list_none():
    assert as_list(None) == []


def test_as_list_rejects_string():
    with pytest.raises(TypeError, match="query must be an object"):
        as_list("DIDAuthentication", label="query")


def test_as_list_rejects_list_of_strings():
    with pytest.raises(TypeError, match="query\\[0\\] must be an object"):
        as_list(["DIDAuthentication"], label="query")
