from src.data.watchlist import Watchlist


class TestWatchlist:
    def test_create_with_custom_symbols(self):
        wl = Watchlist(symbols=["AAPL", "MSFT", "GOOGL"])
        assert len(wl) == 3
        assert wl.symbols == ["AAPL", "MSFT", "GOOGL"]

    def test_add_symbol(self):
        wl = Watchlist(symbols=["AAPL"])
        wl.add("MSFT")
        assert len(wl) == 2
        assert wl.contains("MSFT")

    def test_add_duplicate_ignored(self):
        wl = Watchlist(symbols=["AAPL"])
        wl.add("AAPL")
        assert len(wl) == 1

    def test_add_normalizes_case(self):
        wl = Watchlist(symbols=[])
        wl.add("aapl")
        assert wl.contains("AAPL")

    def test_remove_symbol(self):
        wl = Watchlist(symbols=["AAPL", "MSFT"])
        wl.remove("AAPL")
        assert len(wl) == 1
        assert not wl.contains("AAPL")

    def test_remove_nonexistent_no_error(self):
        wl = Watchlist(symbols=["AAPL"])
        wl.remove("MSFT")  # Should not raise
        assert len(wl) == 1

    def test_contains(self):
        wl = Watchlist(symbols=["AAPL", "MSFT"])
        assert wl.contains("AAPL")
        assert not wl.contains("TSLA")

    def test_contains_case_insensitive(self):
        wl = Watchlist(symbols=["AAPL"])
        assert wl.contains("aapl")
        assert wl.contains("Aapl")

    def test_iteration(self):
        symbols = ["AAPL", "MSFT", "GOOGL"]
        wl = Watchlist(symbols=symbols)
        assert list(wl) == symbols

    def test_symbols_returns_copy(self):
        wl = Watchlist(symbols=["AAPL"])
        symbols = wl.symbols
        symbols.append("MSFT")
        assert len(wl) == 1  # Original unchanged

    def test_empty_watchlist(self):
        wl = Watchlist(symbols=[])
        assert len(wl) == 0
        assert list(wl) == []

    def test_add_strips_whitespace(self):
        wl = Watchlist(symbols=[])
        wl.add("  AAPL  ")
        assert wl.contains("AAPL")
