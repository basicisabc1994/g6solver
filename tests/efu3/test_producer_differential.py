"""Released readers against real report builders with process-only doubles."""
# ruff: noqa: F401 -- pytest collects the re-exported tests and fixtures.
from tests.efu3.test_attack_matrix import tmp_path
from tests.harness.test_report_consumer_parity import (
    process,
    test_producer_report_consumer_parity,
    test_projection_retains_the_started_timeout_and_the_unreached_check,
    test_receipt_report_fields_keep_their_released_meaning,
)
from tests.harness.test_report_consumer_parity import (
    test_each_producer_phase_value_keeps_released_consumer_distinctions,
    test_a_collection_error_beside_a_complete_body_retains_both_distinctions,
    test_replay_persisted_fields_keep_the_exact_failure_disposition,
)

