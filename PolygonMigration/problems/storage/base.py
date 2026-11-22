"""
Provider-independent storage interface for test-case files.

The migration flow only talks to ``TestCaseStorage``. Each cloud provider
(Azure Blob, AWS S3, ...) implements the three abstract methods below, so the
provider can be swapped (via the ``STORAGE_PROVIDER`` env var) without touching
the migration code.

Object layout (identical for every provider)::

    test_cases/{problem_id}/{test_number:02d}      # input file
    test_cases/{problem_id}/{test_number:02d}.a    # output file
    test_cases/{problem_id}/<other files>          # e.g. custom checker
"""
from abc import ABC, abstractmethod


class TestCaseStorage(ABC):
    __test__ = False  # not a test class; stops test runners collecting it

    ROOT = "test_cases"

    # ---- naming helpers (shared so every provider uses the same layout) ----
    @classmethod
    def problem_prefix(cls, problem_id):
        return f"{cls.ROOT}/{problem_id}/"

    @classmethod
    def input_key(cls, problem_id, test_number):
        return f"{cls.ROOT}/{problem_id}/{test_number:02d}"

    @classmethod
    def output_key(cls, problem_id, test_number):
        return f"{cls.ROOT}/{problem_id}/{test_number:02d}.a"

    @classmethod
    def file_key(cls, problem_id, filename):
        return f"{cls.ROOT}/{problem_id}/{filename}"

    # ---- operations every provider must implement ----
    # Error contract: every method raises an exception when the provider reports
    # a failure. Providers must not log an error and carry on, because the caller
    # reports success to the user when no exception is raised.
    @abstractmethod
    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        """Upload one test case (input + output) as two objects. Returns the two keys."""

    @abstractmethod
    def delete_problem_files(self, problem_id, keep=()):
        """
        Delete the objects under ``test_cases/{problem_id}/`` whose key is not in
        ``keep``. With the default empty ``keep`` everything is deleted.
        Returns the number of deleted objects.
        """

    @abstractmethod
    def upload_file(self, problem_id, filename, data):
        """Upload an arbitrary file under ``test_cases/{problem_id}/``. Returns its key."""
