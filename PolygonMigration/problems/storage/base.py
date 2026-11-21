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
    @abstractmethod
    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        """Upload one test case (input + output) as two objects."""

    @abstractmethod
    def delete_problem_files(self, problem_id):
        """Delete every object under ``test_cases/{problem_id}/``."""

    @abstractmethod
    def upload_file(self, problem_id, filename, data):
        """Upload an arbitrary file (bytes) under ``test_cases/{problem_id}/``."""
