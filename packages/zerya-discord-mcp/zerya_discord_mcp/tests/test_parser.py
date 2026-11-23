import pytest

from zerya_discord_mcp.query.parser import QueryError, parse


def test_full_syntax() -> None:
    q = parse('wikipop (fundacja|foundation) "wersja mono" -lego in:#fundacja-grafika from:ola after:2026-09 has:link')
    assert [str(g) for g in q.groups] == ["wikipop", "(fundacja|foundation)", '"wersja mono"']
    assert [t.text for t in q.excludes] == ["lego"]
    assert [(f.key, f.value) for f in q.filters] == [
        ("in", "#fundacja-grafika"),
        ("from", "ola"),
        ("after", "2026-09"),
        ("has", "link"),
    ]


def test_bare_alternatives_and_quoted_filter_value() -> None:
    q = parse('logo|logotyp in:"general chat"')
    assert str(q.groups[0]) == "(logo|logotyp)"
    assert q.filters[0].value == "general chat"


def test_urls_are_words_not_filters() -> None:
    q = parse("https://zerya.dev")
    assert q.groups and not q.filters


@pytest.mark.parametrize(
    ("query", "hint"),
    [
        ('"unclosed', "Unclosed quote"),
        ("(a|b", "Unclosed group"),
        ("has:pdf", "has:link"),
        ("", "Empty query"),
        ("-lego", "exclusions only"),
    ],
)
def test_errors_carry_hints(query: str, hint: str) -> None:
    with pytest.raises(QueryError, match=hint):
        parse(query)
