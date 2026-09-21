from copy import deepcopy

import pytest

from jobagent.config import DEFAULT_PROFILE
from jobagent.scoring import (
    is_prefilter_candidate, score_job, scoring_config, should_retain,
    validate_scoring_config,
)


def scored(title: str, description: str, profile=None):
    return score_job(
        {"title": title, "employer": "Test A/S", "location": "København", "description": description},
        profile or DEFAULT_PROFILE,
    )


def test_senior_angular_banking_is_highly_qualified_but_low_direction_and_retained():
    result = scored(
        "Senior Angular Developer",
        "Angular TypeScript RxJS NgRx HTML SCSS enterprise banking web application in an agile team.",
    )
    assert result.dimension("qualification") >= 80
    assert result.dimension("direction") <= 30
    assert result.score < result.dimension("qualification")
    assert should_retain(result)


def test_safe_pi_planning_and_burn_metrics_have_a_significant_direction_penalty():
    plain = scored("Senior Angular Developer", "Angular TypeScript RxJS in an enterprise banking team.")
    process_heavy = scored(
        "Senior Angular Developer",
        "Angular TypeScript RxJS with SAFe, PI planning, daily Scrum and burndown metrics.",
    )
    assert process_heavy.dimension("qualification") == plain.dimension("qualification")
    assert process_heavy.dimension("direction") <= plain.dimension("direction") - 15
    assert process_heavy.score < plain.score


def test_generic_agile_does_not_receive_the_safe_penalty():
    plain = scored("Senior Angular Developer", "Angular TypeScript RxJS and collaboration.")
    agile = scored("Senior Angular Developer", "Angular TypeScript RxJS and agile collaboration.")
    assert agile.dimension("direction") == plain.dimension("direction")
    assert not any(reason["source"] == "safe_pi_planning" for reason in agile.details["negative_reasons"])


def test_bms_cts_trainee_has_modest_qualification_high_direction_and_survives_filters():
    result = scored(
        "BMS/CTS trainee",
        "Training provided and oplæring in CTS, BMS, PLC, automation, sensors, commissioning and building systems.",
    )
    assert result.dimension("qualification") <= 45
    assert result.dimension("direction") >= 80
    assert result.dimension("curiosity") >= 70
    assert should_retain(result)
    assert is_prefilter_candidate(result)


def test_automation_trade_requirement_penalizes_qualification_but_not_direction():
    base = scored("Automation specialist", "BMS PLC automation and troubleshooting.")
    required = scored(
        "Automation specialist",
        "BMS PLC automation and troubleshooting. Du skal være uddannet automatiktekniker.",
    )
    assert required.dimension("qualification") <= base.dimension("qualification") - 20
    assert required.dimension("direction") == base.dimension("direction")
    assert should_retain(required)


def test_field_service_hardware_and_troubleshooting_has_transferable_qualification_and_direction():
    result = scored(
        "Field service technician",
        "On-site hardware installation, troubleshooting, repair, customer contact and varied tasks.",
    )
    assert result.dimension("qualification") >= 35
    assert result.dimension("direction") >= 65
    assert should_retain(result)


def test_software_plus_iot_hardware_receives_physical_world_interaction_bonus():
    pure = scored("Software developer", "Angular TypeScript web application.")
    physical = scored(
        "Software developer",
        "Angular TypeScript software for IoT sensors, hardware and physical machinery.",
    )
    assert physical.dimension("direction") >= pure.dimension("direction") + 18
    assert any(reason["source"] == "software_plus_physical_world" for reason in physical.details["positive_reasons"])


def test_pure_frontend_is_highly_qualified_with_lower_direction():
    result = scored("Frontend developer", "Angular TypeScript RxJS web application and user interface.")
    assert result.dimension("qualification") >= 65
    assert result.dimension("direction") < 40
    assert should_retain(result)


def test_gamemaster_uses_factual_hobby_evidence_without_turning_it_into_employment():
    with_evidence = scored(
        "Gamemaster til escape room",
        "Storytelling, teater, gæster, mysterier, rollespil and varied tasks.",
    )
    profile_without_evidence = deepcopy(DEFAULT_PROFILE)
    profile_without_evidence["career"]["evidence"] = {}
    without_evidence = scored(
        "Gamemaster til escape room",
        "Storytelling, teater, gæster, mysterier, rollespil and varied tasks.",
        profile_without_evidence,
    )
    assert with_evidence.dimension("curiosity") >= 75
    assert with_evidence.dimension("direction") >= 50
    assert with_evidence.dimension("qualification") > without_evidence.dimension("qualification")
    evidence = with_evidence.details["reasons_by_dimension"]["qualification"]
    assert any(reason["source"] == "profile_evidence_creative_people" for reason in evidence)
    assert DEFAULT_PROFILE["career"]["evidence"]["creative_people"][0]["professional_employment"] is False


@pytest.mark.parametrize("title", ["Negletekniker", "Sygeplejerske", "Nattevagt"])
def test_unrelated_professions_are_hidden_without_a_target_track(title):
    result = scored(title, "Vi søger en erfaren kollega til almindelige opgaver.")
    assert result.hard_reject
    assert not should_retain(result)


def test_currently_learning_changes_direction_and_curiosity_but_never_qualification():
    with_learning = scored("BMS technician", "PLC automation systems.")
    profile_without_learning = deepcopy(DEFAULT_PROFILE)
    profile_without_learning["career"]["learning"] = []
    without_learning = scored("BMS technician", "PLC automation systems.", profile_without_learning)
    assert with_learning.dimension("qualification") == without_learning.dimension("qualification")
    assert with_learning.dimension("direction") > without_learning.dimension("direction")
    assert with_learning.dimension("curiosity") > without_learning.dimension("curiosity")


def test_scores_are_clamped_and_overall_uses_30_50_20_weights():
    result = scored(
        "BMS technician",
        "CTS BMS PLC automation sensors actuators SCADA HMI commissioning installation hardware IoT "
        "troubleshooting repair maintenance customer contact small team autonomy varied tasks training provided.",
    )
    dimensions = [result.dimension(name) for name in ("qualification", "direction", "curiosity")]
    assert all(0 <= value <= 100 for value in dimensions)
    assert result.score == round(dimensions[0] * 0.30 + dimensions[1] * 0.50 + dimensions[2] * 0.20)


def test_scoring_configuration_rejects_invalid_weight_totals():
    config = deepcopy(scoring_config())
    validate_scoring_config(config)
    config["overall_score"]["formula"]["direction"] = 0.1
    with pytest.raises(ValueError, match="tilsammen være 1"):
        validate_scoring_config(config)
