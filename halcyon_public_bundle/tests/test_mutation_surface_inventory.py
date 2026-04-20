from pathlib import Path

from halcyon_core.governance.mutation_surface import (
    EXCLUDED_CANDIDATES,
    MUTATION_SURFACES,
    MutationSurface,
    MutationSurfaceInventoryError,
    governed_surfaces,
    non_claim_safe_surfaces,
    scan_for_mutation_candidates,
    unclassified_mutation_candidates,
    validate_candidate_exclusions,
    validate_mutation_surface,
    validate_mutation_surface_inventory,
)


def test_manual_mutation_surface_inventory_is_valid():
    validate_mutation_surface_inventory()


def test_every_governed_surface_has_test_coverage_and_is_claim_safe():
    governed = governed_surfaces()

    assert governed
    for surface in governed:
        assert surface.requires_tests is True
        assert surface.claim_safe is True
        assert surface.test_ids, surface.name


def test_static_scan_candidates_are_classified_or_explicitly_excluded():
    candidates = scan_for_mutation_candidates()
    unmatched = unclassified_mutation_candidates(candidates)

    assert candidates
    assert not unmatched, [
        f"{candidate.file}:{candidate.line}:{candidate.pattern}:{candidate.snippet}"
        for candidate in unmatched
    ]


def test_static_scan_exclusions_have_reasons():
    validate_candidate_exclusions()
    assert all(exclusion.reason for exclusion in EXCLUDED_CANDIDATES)


def test_referenced_surface_tests_exist():
    for surface in MUTATION_SURFACES:
        for test_id in surface.test_ids:
            file_name, _, test_name = test_id.partition("::")
            assert file_name, surface.name
            assert test_name.startswith("test_"), test_id
            test_file = Path(file_name)
            assert test_file.exists(), test_id
            assert f"def {test_name}" in test_file.read_text(), test_id


def test_bypass_and_quarantined_surfaces_are_not_claim_safe():
    risky = [
        surface
        for surface in MUTATION_SURFACES
        if surface.classification in {"bypass_risk", "legacy_quarantined", "scaffold"}
    ]

    assert risky
    assert all(surface.claim_safe is False for surface in risky)


def test_reject_and_update_decision_are_now_governed():
    """Closed 2026-04-19: reject path guarded by require_mutation_context."""
    governed = {surface.name for surface in MUTATION_SURFACES if surface.classification == "governed"}
    assert "direct_memory_proposal_reject" in governed
    assert "direct_memory_proposal_update_decision" in governed


def test_unknown_or_unclassified_surface_fails_inventory_rules():
    bad = MutationSurface(
        name="unknown_write",
        location="halcyon_core/example.py::unknown_write",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(),
    )

    try:
        validate_mutation_surface(bad)
    except MutationSurfaceInventoryError as exc:
        assert "needs at least one test id" in str(exc)
    else:
        raise AssertionError("uncovered governed surface should fail validation")


def test_non_claim_safe_surfaces_are_explicitly_listed():
    names = {surface.name for surface in non_claim_safe_surfaces()}
    assert "cli_subprocess_smoke_tests" in names
    assert "direct_memory_proposal_reject" not in names
    assert "direct_memory_proposal_update_decision" not in names
