import hashlib

import pytest
import requests

from abaqus_sim_agent.coding import static_preflight as static


def configure_payload(monkeypatch, payload):
    monkeypatch.setattr(
        static, "STATIC_WHEEL_SHA256", hashlib.sha256(payload).hexdigest()
    )
    monkeypatch.setattr(static, "STATIC_WHEEL_SIZE", len(payload))


class Download:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.payload


def test_verified_download_is_reused_without_network(monkeypatch):
    payload = b"fixed artifact"
    configure_payload(monkeypatch, payload)
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Download(payload)

    monkeypatch.setattr(static.requests, "get", get)
    path = static.ensure_managed_static_wheel()
    assert path.read_bytes() == payload
    assert static.ensure_managed_static_wheel() == path
    assert calls == [static.STATIC_WHEEL_URL]


@pytest.mark.parametrize("payload", [b"wrong", b"too much data"])
def test_invalid_download_is_never_published(monkeypatch, payload):
    configure_payload(monkeypatch, b"right")
    monkeypatch.setattr(static.requests, "get", lambda *a, **kw: Download(payload))
    with pytest.raises(ValueError, match=r"size|checksum"):
        static.ensure_managed_static_wheel()
    path, _ = static.managed_static_paths()
    assert not path.exists()
    assert not list(path.parent.glob("*.tmp"))


def test_corrupt_cache_is_not_silently_replaced(monkeypatch):
    configure_payload(monkeypatch, b"right")
    path, _ = static.managed_static_paths()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="checksum"):
        static.ensure_managed_static_wheel()
    assert path.read_bytes() == b"wrong"


def test_managed_source_symlink_cannot_bypass_the_checksum(tmp_path):
    wheel, cache = static.managed_static_paths()
    wheel.parent.mkdir(parents=True)
    target = tmp_path / "untrusted.whl"
    target.write_bytes(b"untrusted")
    try:
        wheel.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink"):
        static.prepare_static_preflight_source(
            mode="on", wheel=wheel, cache_dir=cache, workspace=tmp_path / "workspace"
        )


def test_download_failure_preserves_unavailable_state(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("offline fixture")

    monkeypatch.setattr(static.requests, "get", unavailable)
    with pytest.raises(requests.ConnectionError, match="offline fixture"):
        static.ensure_managed_static_wheel()
    path, _ = static.managed_static_paths()
    assert not path.exists()


def test_disabled_preflight_never_downloads(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("off mode must not prepare resources")

    monkeypatch.setattr(static, "ensure_managed_static_wheel", forbidden)
    assert (
        static.prepare_static_preflight_source(
            mode="off", wheel=None, cache_dir=None, workspace=tmp_path
        )
        is None
    )
