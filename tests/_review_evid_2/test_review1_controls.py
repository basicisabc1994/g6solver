"""Run the original acceptance cases with all scratch files under review 2."""
from tests._review_evid_2.test_remaining_boundaries import isolated, tmp_path
from tests._review_evid_1.test_evidence_boundaries import (
    test_certificate_cites_all_task_observations_and_labels,
    test_current_review_card_uses_current_job,
    test_fresh_reply_custody_storage_and_decided_fields_control,
    test_loop_repairs_require_a_different_artifact,
    test_pre_call_refusal_does_not_count_as_checked,
    test_readable_export_own_retention_control,
    test_replayed_card_respects_current_record_custody,
)
