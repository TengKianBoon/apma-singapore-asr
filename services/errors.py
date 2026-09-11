class JobExistsError(Exception):
    """Raised when attempting to create or run a job with an existing job_id."""


class StepFailedError(Exception):
    """Raised when a pipeline step fails irrecoverably."""


class CostLimitExceededError(Exception):
    """Raised when estimated job cost exceeds configured maximum."""
