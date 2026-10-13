"""Errors the engine raises. Apps may import this module."""


class DatabaseCheckError(Exception):
    """The database is not one Bowerstone may run against.

    Attributes:
        problems: Each reason the database was refused, in plain words.
    """

    def __init__(self, problems: list[str]) -> None:
        """Create the error from the problems the database check found.

        Args:
            problems: Each reason the database was refused. Must not be empty.

        Raises:
            ValueError: If problems is empty.

        Example:
            >>> str(DatabaseCheckError(["the server is PostgreSQL 17, not 18"]))
            'The database check failed: the server is PostgreSQL 17, not 18.'
        """
        if not problems:
            raise ValueError("a DatabaseCheckError needs at least one problem")
        self.problems = tuple(problems)
        super().__init__("The database check failed: " + "; ".join(problems) + ".")


class MigrationError(Exception):
    """A migration could not be read, checked, or run.

    The message names the module and, where there is one, the file.

    Example:
        >>> raise MigrationError("core: 0002_parties.sql has no down file")
        Traceback (most recent call last):
        MigrationError: core: 0002_parties.sql has no down file
    """
