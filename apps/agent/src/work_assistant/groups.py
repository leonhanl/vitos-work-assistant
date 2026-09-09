"""Entra security group labels reported to the AI Gateway."""

from __future__ import annotations

from collections.abc import Mapping


def map_group_labels(
    raw_groups: object,
    labels: Mapping[str, str],
) -> tuple[str, ...]:
    """Map Entra group object IDs to this application's own group labels.

    `labels` doubles as an allowlist: only groups meaningful to this application
    are reported, so unrelated directory membership never leaves the app. Labels
    are used verbatim as gateway metadata key suffixes, so they must already be
    snake_case.
    """
    if not isinstance(raw_groups, list):
        return ()

    normalized = {
        object_id.strip().lower(): label
        for object_id, label in labels.items()
        if object_id.strip()
    }
    matched = {
        normalized[group.strip().lower()]
        for group in raw_groups
        if isinstance(group, str) and group.strip().lower() in normalized
    }
    return tuple(sorted(matched))
