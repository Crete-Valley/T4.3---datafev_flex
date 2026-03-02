import time

from api.cache import TTLObjectCache


def test_ttl_cache_put_get_and_delete():
    cache = TTLObjectCache(ttl_seconds=10, max_items=4)
    key = cache.put({"x": 1})

    assert cache.get(key) == {"x": 1}
    assert cache.size() == 1

    cache.delete(key)
    assert cache.get(key) is None
    assert cache.size() == 0


def test_ttl_cache_evicts_expired_items():
    cache = TTLObjectCache(ttl_seconds=1, max_items=4)
    key = cache.put("value")
    assert cache.get(key) == "value"

    time.sleep(1.1)
    assert cache.get(key) is None
    assert cache.size() == 0


def test_ttl_cache_evicts_oldest_on_overflow():
    cache = TTLObjectCache(ttl_seconds=60, max_items=2)
    key1 = cache.put("v1")
    key2 = cache.put("v2")
    key3 = cache.put("v3")

    assert cache.get(key1) is None
    assert cache.get(key2) == "v2"
    assert cache.get(key3) == "v3"
    assert cache.size() == 2


def test_ttl_cache_put_with_key_inserts_and_overwrites():
    cache = TTLObjectCache(ttl_seconds=60, max_items=4)
    cache.put_with_key("job-1", {"x": 1})
    assert cache.get("job-1") == {"x": 1}

    cache.put_with_key("job-1", {"x": 2})
    assert cache.get("job-1") == {"x": 2}
    assert cache.size() == 1
