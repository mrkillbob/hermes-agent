"""Body-free public diagnostics for unchanged startup writer refusal gates."""

_DETAILS = {
    "admission": frozenset({"unsafe_lock_file", "lock_identity_changed", "lock_unavailable"}),
    "writer_inventory": frozenset({
        "checkpoint_inventory_invalid", "record_json_invalid", "record_shape_invalid",
        "record_birth_invalid", "unsafe_directory", "unsafe_reference", "inventory_limit",
        "reference_unavailable", "short_read", "directory_identity_changed",
        "membership_changed", "named_reference_changed", "opened_reference_changed", "record_bytes_changed",
    }),
}


class WriterRefusal(RuntimeError):
    reason = "startup_hold_writers_busy"

    def __init__(self, stage, detail):
        if stage not in _DETAILS or detail not in _DETAILS[stage]:
            raise ValueError("unknown writer refusal diagnostic")
        super().__init__(self.reason)
        self.stage, self.detail = stage, detail


def public_writer_diagnostic(exc):
    """Only our exact type and fixed vocabulary cross the operator JSON boundary."""
    if (type(exc) is WriterRefusal and type(exc.stage) is str and type(exc.detail) is str
            and exc.stage in _DETAILS
            and exc.detail in _DETAILS[exc.stage]):
        return {"stage": exc.stage, "detail": exc.detail}
    return None
