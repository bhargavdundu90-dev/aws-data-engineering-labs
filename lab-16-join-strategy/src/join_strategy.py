"""Pick a Spark join strategy from table sizes and key skew (no Spark needed)."""
from collections import Counter

DEFAULT_BROADCAST_THRESHOLD = 10 * 1024 * 1024  # spark.sql.autoBroadcastJoinThreshold


def skew_ratio(keys):
    """Share of rows held by the most common key (0..1)."""
    keys = list(keys)
    if not keys:
        return 0.0
    return Counter(keys).most_common(1)[0][1] / len(keys)


def choose_strategy(left_bytes, right_bytes, left_keys=(), skew_limit=0.2,
                    threshold=DEFAULT_BROADCAST_THRESHOLD):
    """Return 'broadcast', 'salted_shuffle' or 'sort_merge'."""
    if min(left_bytes, right_bytes) < 0:
        raise ValueError("sizes must be non-negative")
    if min(left_bytes, right_bytes) <= threshold:
        return "broadcast"
    if skew_ratio(left_keys) > skew_limit:
        return "salted_shuffle"
    return "sort_merge"


def salt_key(key, row_id, buckets=8):
    """Spread a hot key over `buckets` sub-keys deterministically."""
    return f"{key}#{row_id % buckets}"
