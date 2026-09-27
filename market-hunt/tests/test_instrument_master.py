# ISIN-keyed instrument resolution. Pure, no Mongo, no network.
#
# The motivating failure: a company renames its ticker. Symbol-keyed lookup stops
# resolving, the row drops out of the rebalance, and nothing says why. ISIN does
# not change on a rename, so it is the primary key.
import instrument_master as im

DOCS = [
    {"isin": "INE009A01021", "symbol": "INFY", "series": "EQ",
     "dhan_security_id": "1594", "kite_tradingsymbol": "INFY"},
    {"isin": "INE467B01029", "symbol": "TCS", "series": "EQ",
     "dhan_security_id": "11536", "kite_tradingsymbol": "TCS"},
    # same identity listed twice — EQ must win over BE
    {"isin": "INE123A01010", "symbol": "DUAL", "series": "BE",
     "dhan_security_id": "999-BE", "kite_tradingsymbol": "DUAL"},
    {"isin": "INE123A01010", "symbol": "DUAL", "series": "EQ",
     "dhan_security_id": "999-EQ", "kite_tradingsymbol": "DUAL"},
    # known to the master, but no Kite identifier
    {"isin": "INE555A01010", "symbol": "NOKITE", "series": "EQ",
     "dhan_security_id": "777", "kite_tradingsymbol": ""},
]

IDX = im.build_index(DOCS)


def _row(symbol, isin=""):
    return {"symbol": symbol, "isin": isin}


def test_resolves_by_isin_after_a_ticker_rename():
    """The whole point: the plan carries the OLD symbol, the master has the NEW
    one, and the ISIN still ties them together."""
    rows = [_row("INFOSYS_OLD", "INE009A01021")]
    got, unresolved = im.resolve(rows, IDX, "dhan")
    assert got == {"INFOSYS_OLD": "1594"}   # keyed by the row's symbol, resolved via ISIN
    assert unresolved == []


def test_symbol_fallback_when_row_has_no_isin():
    got, unresolved = im.resolve([_row("TCS")], IDX, "dhan")
    assert got == {"TCS": "11536"} and unresolved == []


def test_isin_wins_over_a_stale_symbol_collision():
    """Row's symbol points at one instrument, its ISIN at another. ISIN decides."""
    got, _ = im.resolve([_row("TCS", "INE009A01021")], IDX, "dhan")
    assert got == {"TCS": "1594"}           # INFY's id, chosen by ISIN not by symbol


def test_eq_series_preferred_on_collision():
    got, _ = im.resolve([_row("DUAL", "INE123A01010")], IDX, "dhan")
    assert got == {"DUAL": "999-EQ"}
    got, _ = im.resolve([_row("DUAL")], IDX, "dhan")
    assert got == {"DUAL": "999-EQ"}        # same via the symbol table


def test_kite_gets_tradingsymbol_not_security_id():
    got, _ = im.resolve([_row("TCS", "INE467B01029")], IDX, "kite")
    assert got == {"TCS": "TCS"}


def test_unknown_identity_is_reported_not_guessed():
    got, unresolved = im.resolve([_row("MYSTERY", "INE000X01010")], IDX, "dhan")
    assert got == {}
    assert len(unresolved) == 1 and "MYSTERY" in unresolved[0]


def test_known_instrument_missing_that_brokers_identifier():
    got, unresolved = im.resolve([_row("NOKITE", "INE555A01010")], IDX, "kite")
    assert got == {}
    assert "no kite identifier" in unresolved[0]
    # ...but Dhan can still trade it
    got_d, unresolved_d = im.resolve([_row("NOKITE", "INE555A01010")], IDX, "dhan")
    assert got_d == {"NOKITE": "777"} and unresolved_d == []


def test_unknown_broker_resolves_nothing():
    got, unresolved = im.resolve([_row("TCS")], IDX, "zerodha_typo")
    assert got == {} and unresolved


def test_case_and_whitespace_are_normalised():
    got, _ = im.resolve([{"symbol": " tcs ", "isin": " ine467b01029 "}], IDX, "dhan")
    assert got == {"tcs": "11536"}          # key preserves the row's own spelling


def test_index_ignores_docs_with_neither_key():
    idx = im.build_index([{"series": "EQ", "dhan_security_id": "1"}])
    assert idx["by_isin"] == {} and idx["by_symbol"] == {}


def test_partial_resolution_reports_only_the_failures():
    rows = [_row("TCS", "INE467B01029"), _row("MYSTERY", "INE000X01010")]
    got, unresolved = im.resolve(rows, IDX, "dhan")
    assert got == {"TCS": "11536"}
    assert len(unresolved) == 1 and "MYSTERY" in unresolved[0]
