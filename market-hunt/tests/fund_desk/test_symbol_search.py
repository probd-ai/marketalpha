"""Symbol search for the desk's symbol inputs (other holdings): ranks the same universe `known()` accepts."""
from fund import service

U = [
    {"symbol": "HNGSNGBEES", "name": "NIP IND ETF HANGSENG BEES"},
    {"symbol": "GOLDBEES", "name": "NIPPON INDIA ETF GOLD BEES"},
    {"symbol": "BEL", "name": "Bharat Electronics Ltd."},
    {"symbol": "SAIL", "name": "Steel Authority of India Ltd."},
    {"symbol": "M&M", "name": "Mahindra & Mahindra Ltd."},
    {"symbol": "HINDALCO", "name": "Hindalco Industries Ltd."},
]


def syms(q, **kw):
    return [r["symbol"] for r in service.search_symbols(q, U, **kw)]


def test_exact_then_prefix_then_substring_then_name():
    assert syms("bel") == ["BEL"]                                 # exact first; no other symbol/name contains BEL
    assert syms("h")[:2] == ["HINDALCO", "HNGSNGBEES"]            # prefix matches, alphabetical
    assert syms("BEES") == ["GOLDBEES", "HNGSNGBEES"]             # symbol substring
    assert syms("hang") == ["HNGSNGBEES"]                         # company-name match (typo-proof route)
    assert syms("steel") == ["SAIL"]


def test_blank_and_special_characters():
    assert syms("   ") == []
    assert syms("m&m") == ["M&M"]                                 # no regex: & and other symbols are literal
    assert syms(".*") == []


def test_limit_and_result_shape():
    assert len(service.search_symbols("e", U, limit=2)) == 2
    assert service.search_symbols("sail", U)[0] == {"symbol": "SAIL", "name": "Steel Authority of India Ltd."}


def test_duplicate_symbols_collapse():
    dup = U + [{"symbol": "SAIL", "name": "SAIL"}]
    assert syms("sail", ) == ["SAIL"]
    assert [r["symbol"] for r in service.search_symbols("sail", dup)] == ["SAIL"]
