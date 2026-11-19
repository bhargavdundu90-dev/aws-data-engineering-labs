"""Local model of Glue job-bookmark behaviour for S3 sources.

Glue tracks processed S3 objects by (key, last_modified). A file is picked up again
only if it is new or its modification time changed. This module mimics that so the
incremental-load logic can be unit tested without AWS.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class S3Object:
    key: str
    last_modified: int  # epoch seconds


@dataclass
class Bookmark:
    seen: dict[str, int] = field(default_factory=dict)

    def pending(self, objects: list[S3Object]) -> list[S3Object]:
        """Objects not yet processed, or rewritten since they were processed."""
        return sorted(
            (o for o in objects if self.seen.get(o.key) != o.last_modified),
            key=lambda o: o.key,
        )

    def commit(self, processed: list[S3Object]) -> None:
        """Advance the bookmark; call only after the job succeeded."""
        for o in processed:
            self.seen[o.key] = o.last_modified

    def reset(self) -> None:
        self.seen.clear()


def run_job(bookmark: Bookmark, objects: list[S3Object], fail: bool = False) -> list[str]:
    batch = bookmark.pending(objects)
    if fail:
        return []  # bookmark not advanced: next run retries the same files
    bookmark.commit(batch)
    return [o.key for o in batch]
