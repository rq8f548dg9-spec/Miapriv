import time

from server.store import TTLStore


def test_salva_e_rilegge():
    store = TTLStore(ttl_seconds=60)
    store.put("a", 1)
    assert store.get("a") == 1
    assert store.get("manca") is None


def test_scade():
    store = TTLStore(ttl_seconds=0)
    store.put("a", 1)
    time.sleep(0.01)
    assert store.get("a") is None


def test_non_cresce_oltre_il_massimo():
    store = TTLStore(ttl_seconds=60, max_items=3)
    for i in range(10):
        store.put(str(i), i)
    assert len(store) <= 3
    assert store.get("9") == 9
