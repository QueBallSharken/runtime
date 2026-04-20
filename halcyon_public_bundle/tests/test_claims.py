from halcyon_core.governance.claims import ClaimClassifier, ClaimEvidence, classify_claim

PASSING_CONDITIONS = {
    "C1": "pass",
    "C2": "pass",
    "C3": "pass",
    "C4": "pass",
    "C5": "pass",
    "C6": "pass",
}


def test_breach_notice_overrides_other_evidence():
    claim = classify_claim(PASSING_CONDITIONS, breach_notices=("chain liveness lost",))
    assert claim.classification == "governance_breach"
    assert claim.breach is True
    assert claim.bbis_score == 0
    assert claim.binary_override_triggered is True


def test_failed_condition_collapses_to_observability_and_zeros_mapped_family():
    conditions = dict(PASSING_CONDITIONS, C3="fail")
    claim = classify_claim(conditions)
    assert claim.classification == "observability_only"
    assert claim.binary_override_triggered is True
    assert claim.f_family_rollups["F3"] == 0


def test_failed_c6_condition_causes_observability_and_nulls_f6():
    conditions = dict(PASSING_CONDITIONS, C6="fail")
    claim = classify_claim(conditions)
    assert claim.classification == "observability_only"
    assert claim.binary_override_triggered is True
    assert claim.f_family_rollups["F6"] == 0


def test_missing_condition_is_not_inferred_as_pass():
    claim = classify_claim({"C1": "pass", "C2": "pass"})
    assert claim.classification == "observability_only"
    assert claim.condition_summary["C3"] == "unknown"
    assert "missing evidence" in claim.reasons[0]


def test_documented_scope_break_can_be_strong_segment_governance():
    claim = classify_claim(
        dict(PASSING_CONDITIONS, C4="partial"),
        scope_limitations=("claim ends at external handoff ext_01",),
        segment_documented=True,
    )
    assert claim.classification == "strong_segment_governance"
    assert claim.f_family_rollups["F4"] == 1
    assert claim.f_family_rollups["F6"] == 1


def test_partial_condition_without_documented_segment_is_partial_governance():
    claim = classify_claim(dict(PASSING_CONDITIONS, C5="partial"))
    assert claim.classification == "partial_governance"
    assert claim.f_family_rollups["F3"] == 1


def test_detector_cap_downgrades_full_path_claim():
    claim = classify_claim(PASSING_CONDITIONS, detector_caps=("sbac_detector_trust_partial",))
    assert claim.classification == "partial_governance"
    assert "detector evidence caps" in claim.reasons[0]


def test_detector_cap_with_documented_scope_allows_segment_claim():
    claim = classify_claim(
        PASSING_CONDITIONS,
        detector_caps=("external_adapter_only",),
        scope_limitations=("only local adapter segment instrumented",),
        segment_documented=True,
    )
    assert claim.classification == "strong_segment_governance"


def test_all_conditions_pass_without_caps_is_strong_full_path():
    claim = ClaimClassifier().classify(
        ClaimEvidence(execution_path_id="path_clean", conditions=PASSING_CONDITIONS)
    )
    assert claim.artifact_type == "final_claim"
    assert claim.execution_path_id == "path_clean"
    assert claim.classification == "strong_full_path_governance"
    assert claim.bbis_score == 12
    assert claim.to_dict()["classification"] == "strong_full_path_governance"
