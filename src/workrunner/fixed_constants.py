from contextlib import contextmanager
from contextvars import ContextVar

from .native_text_codec import FixedAlias as FixedAlias, fixed_buffer, word_buffer

_DISPLAY_TEXT_OVERRIDES = {
    b'training.label.batch_acceptance_bound': 'acceptance bound',
    b'training.label.tensor_commitment_tree': 'Commitment',
    b'training.log.accepted_batch_tile_location': 'Verified batch tile at ',
    b'training.log.attestation_record_assembly': 'Assembling attestation record from plain_proof',
    b'training.log.attestation_record_generated': 'Generated attestation record',
    b'training.log.batch_header_verified_no_submissions': 'Authenticated batch received: header=%d bytes, acceptance bound=%d bits; logged in without executing GPU batches',
    b'training.log.batch_submission_accepted': 'S3 accepted training submission job=%s',
    b'training.log.batch_submission_rejected': 'S3 rejected training submission job=%s: %s',
    b'training.validation.acceptance_bound_interval_prefix': 'acceptance_bound must be in the range acceptance_bound=',
    b'training.validation.account_identity_required_suffix': ' must contain a valid S6 account address',
    b'training.validation.accelerator_attestation_digest_length': 'GPU attestation digest must be exactly 32 bytes',
    b'training.validation.accelerator_reference_digest_mismatch': 'GPU attestation digest differs from the official CPU reference',
    b'training.validation.accelerator_tensor_commitment_mismatch': 'native GPU commitment root differs from the official CPU reference tree',
    b'training.validation.accelerator_throughput_range': 'training rate must be a nonnegative finite number in units/s',
    b'training.validation.batch_and_compact_attestation_required': 'S7 submission requires its job and compact attestation',
    b'training.validation.batch_device_seed_length': 'S7 worker seed must contain eight bytes',
    b'training.validation.batch_seed_pair_length': 'S7 seeds must each contain exactly eight bytes',
    b'training.validation.batch_submission_device_seed_mismatch': 'S7 submission worker seed does not match its job',
    b'training.validation.batch_submission_dimensions_type': 'S7 submission dimensions must be integers',
    b'training.validation.batch_submission_fields_invalid': 'Invalid S7 submission fields',
    b'training.validation.batch_submission_outside_acceptance_bound': 'training submission does not meet the exact S3 acceptance bound',
    b'training.validation.batch_tile_index_order_and_seed_row': 'S7 tile indices must be sorted, unique and outside the seed row',
    b'training.validation.compact_attestation_required': 'S7 verification requires a compact attestation',
    b'training.validation.coordinator_closed_before_acknowledgement': 'S3 closed before acknowledging the submission',
    b'training.validation.coordinator_seed_required': "S7 training requires the coordinator's eight-byte session seed",
    b'training.validation.coordinator_seed_update_schema': 'Unsupported S7 session-seed update schema',
    b'training.validation.coordinator_submission_acknowledgement_timeout': 'S3 submission acknowledgement timed out',
    b'training.validation.coordinator_submission_attestation_version': 'unsupported S6 certificate version for S3 submission',
    b'training.validation.coordinator_submission_rejected': 'S3 rejected submission',
    b'training.validation.coordinator_submission_send_failed': 'S3 submission send failed',
    b'training.validation.coordinator_submission_verification_prefix': 'S3 submission rejected by official verification: ',
    b'training.validation.coordinator_tile_coordinates_type': 'S3 submission tile coordinates must be integers',
    b'training.validation.cpu_attestation_tile_incomplete': 'official CPU reference produced no complete attestation tile',
    b'training.validation.local_attestation_generation_unavailable': 'Local ZK certificate generation is unavailable; WR and plain S3 submissions do not require it',
    b'training.validation.tensor_commitment_header_mismatch': 'Commitment proof does not match header and configuration',
    b'training.validation.tensor_commitment_input_empty': 'Commitment data must not be empty',
    b'training.validation.tensor_commitment_leaf_count_mismatch': 'Commitment tree leaf count differs from declared dimensions',
    b'training.validation.tensor_commitment_leaf_count_positive': 'Commitment tree requires a positive leaf count',
    b'training.validation.tensor_commitment_leaf_order': 'Commitment leaf indices must be sorted, unique and in range',
    b'training.validation.tensor_commitment_leaf_range': 'Commitment leaf indices are out of range',
    b'training.validation.tensor_commitment_matrix_coverage': 'Commitment leaves do not cover required matrix bytes',
    b'training.validation.tensor_commitment_slice_range': 'invalid Commitment extraction range',
}

_neutral_text = ContextVar('neutral_protocol_text', default=False)


@contextmanager
def neutral_protocol_text():

    token = _neutral_text.set(True)
    try:
        yield
    finally:
        _neutral_text.reset(token)


def using_neutral_protocol_text():
    return _neutral_text.get()


@contextmanager
def constant_buffer(original: bytes | str):
    buffer = word_buffer(original) if isinstance(original, str) else fixed_buffer(original)
    with buffer as value:
        yield value


def constant_bytes(original: bytes | str) -> bytes:
    with constant_buffer(original) as value:
        return bytes(value)


def constant_text(original: bytes | str) -> str:

    if isinstance(original, bytes):
        override = _DISPLAY_TEXT_OVERRIDES.get(bytes(original))
        if override is not None:
            return override
    if isinstance(original, str) and _neutral_text.get():
        return original
    with constant_buffer(original) as value:
        return value.decode("utf-8")


def constant_ascii_matches(original: bytes | str, value: object) -> bool:

    if not isinstance(value, str):
        return False
    if isinstance(original, str) and _neutral_text.get():
        return original == value
    with constant_buffer(original) as expected:
        return len(value) == len(expected) and all(
            ord(character) == byte for character, byte in zip(value, expected)
        )
