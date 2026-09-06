class UpdateError(RuntimeError):
    """Base class for safe-update failures."""


class SignatureBackendUnavailable(UpdateError):
    pass


class ManifestError(UpdateError):
    pass


class DownloadError(UpdateError):
    pass


class ArchiveError(UpdateError):
    pass


class UpdateBusyError(UpdateError):
    pass


class ActivationError(UpdateError):
    pass
