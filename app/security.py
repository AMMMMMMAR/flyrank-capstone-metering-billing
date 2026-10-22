import hashlib


def hash_api_key(raw_key: str) -> str:
    """Turn a secret API key into a fixed-length fingerprint (64 hex characters).

    We store only the fingerprint, never the real key.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
