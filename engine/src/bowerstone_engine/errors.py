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


class AppManifestError(Exception):
    """An app's manifest breaks the rules every manifest must meet.

    The message names the app, or the entry point that declared it.

    Example:
        >>> raise AppManifestError("'Core' is not a valid app key")
        Traceback (most recent call last):
        AppManifestError: 'Core' is not a valid app key
    """


class InstallError(Exception):
    """An app could not be installed, upgraded, or removed.

    The message says which app and why, such as a missing dependency.

    Example:
        >>> raise InstallError("invoicing needs accounting, which no package provides")
        Traceback (most recent call last):
        InstallError: invoicing needs accounting, which no package provides
    """
