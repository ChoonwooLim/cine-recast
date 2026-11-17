class CineRecastError(Exception):
    """A user-facing error. The CLI prints it as 'error: <message>' and exits with 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
