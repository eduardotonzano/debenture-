from __future__ import annotations

from datetime import timedelta

from debenture_search.cache import SqliteCache


def test_get_retorna_none_quando_vazio(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    assert cache.get("SND", "estoque", "TEPA23") is None


def test_set_depois_get_retorna_o_valor(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    cache.set("SND", "estoque", "TEPA23", "<html>conteudo</html>")
    assert cache.get("SND", "estoque", "TEPA23") == "<html>conteudo</html>"


def test_expira_apos_ttl(tmp_path, monkeypatch) -> None:
    import debenture_search.cache as cache_module
    from datetime import datetime

    cache = SqliteCache(tmp_path / "cache.sqlite3", ttl=timedelta(seconds=1))
    cache.set("SND", "estoque", "TEPA23", "<html></html>")

    fake_agora = datetime.utcnow() + timedelta(seconds=2)

    class FakeDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return fake_agora

    monkeypatch.setattr(cache_module, "datetime", FakeDatetime)
    assert cache.get("SND", "estoque", "TEPA23") is None


def test_json_helpers(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    cache.set_json("SND", "search:isin", "BRTEPADBS001", {"a": 1})
    assert cache.get_json("SND", "search:isin", "BRTEPADBS001") == {"a": 1}
