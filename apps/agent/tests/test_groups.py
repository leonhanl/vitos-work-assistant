from work_assistant.groups import map_group_labels

LABELS = {
    "11111111-1111-1111-1111-111111111111": "it_admin",
    "22222222-2222-2222-2222-222222222222": "finance",
}


def test_known_groups_map_to_sorted_labels() -> None:
    assert map_group_labels(
        [
            "22222222-2222-2222-2222-222222222222",
            "11111111-1111-1111-1111-111111111111",
        ],
        LABELS,
    ) == ("finance", "it_admin")


def test_unmapped_groups_are_dropped() -> None:
    assert map_group_labels(
        [
            "99999999-9999-9999-9999-999999999999",
            "11111111-1111-1111-1111-111111111111",
        ],
        LABELS,
    ) == ("it_admin",)


def test_object_ids_match_case_insensitively() -> None:
    assert map_group_labels(
        ["11111111-1111-1111-1111-111111111111".upper()],
        LABELS,
    ) == ("it_admin",)


def test_duplicate_memberships_collapse() -> None:
    object_id = "11111111-1111-1111-1111-111111111111"
    assert map_group_labels([object_id, object_id], LABELS) == ("it_admin",)


def test_missing_or_malformed_claim_reports_no_groups() -> None:
    # Entra omits the claim entirely, and replaces it with _claim_names on overage.
    assert map_group_labels(None, LABELS) == ()
    assert map_group_labels("11111111-1111-1111-1111-111111111111", LABELS) == ()
    assert map_group_labels([], LABELS) == ()


def test_no_configured_labels_reports_no_groups() -> None:
    assert map_group_labels(["11111111-1111-1111-1111-111111111111"], {}) == ()
