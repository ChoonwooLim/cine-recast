class CineRecastError(Exception):
    """A user-facing error. The CLI prints it as 'error: <message>' and exits with 2.

    Any other exception that reaches the CLI is a defect: it prints one 'error: unexpected failure'
    line and exits with 1.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
