"""
Tests for the storage layer and the storage migration.

They use fakes and mocks, so they need no database, no Polygon access and no
cloud account. Run them with:  python manage.py test problems
"""
from unittest import mock

from django.test import SimpleTestCase

from problems.polygon_api import PolygonAPI
from problems.storage.azure import AzureStorage
from problems.storage.base import TestCaseStorage
from problems.storage.s3 import S3Storage


class FakeStorage(TestCaseStorage):
    """In-memory storage. `fail_on` makes the upload of one key raise."""

    def __init__(self, existing=None, fail_on=None):
        self.files = dict(existing or {})
        self.fail_on = fail_on

    def _put(self, key, data):
        if key == self.fail_on:
            raise RuntimeError(f"upload of {key} failed")
        self.files[key] = data
        return key

    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        return [
            self._put(self.input_key(problem_id, test_number), input_data),
            self._put(self.output_key(problem_id, test_number), output_data),
        ]

    def upload_file(self, problem_id, filename, data):
        return self._put(self.file_key(problem_id, filename), data)

    def delete_problem_files(self, problem_id, keep=()):
        prefix = self.problem_prefix(problem_id)
        stale = [k for k in self.files if k.startswith(prefix) and k not in set(keep)]
        for key in stale:
            del self.files[key]
        return len(stale)


def make_tests(count):
    return [{'index': i, 'input': f"in{i}\n", 'output': f"out{i}\n"} for i in range(1, count + 1)]


class MigrateToStorageTests(SimpleTestCase):
    """PolygonAPI.migrate_to_storage: upload first, delete stale files last."""

    def run_migration(self, storage, tests, checker=None, checker_error=None):
        api = PolygonAPI()
        checker_patch = (
            mock.patch.object(api, 'upload_custom_checker', side_effect=checker_error)
            if checker_error
            else mock.patch.object(api, 'upload_custom_checker', return_value=checker)
        )
        with mock.patch.object(api, 'get_test_cases_from_redis', return_value=tests), checker_patch:
            return api.migrate_to_storage('999', storage, db_problem_id=7)

    def old_files(self, count):
        files = {}
        for i in range(1, count + 1):
            files[f"test_cases/7/{i:02d}"] = "old"
            files[f"test_cases/7/{i:02d}.a"] = "old"
        return files

    def test_uploads_use_the_required_layout(self):
        storage = FakeStorage()
        result = self.run_migration(storage, make_tests(2))
        self.assertEqual(
            sorted(storage.files),
            ["test_cases/7/01", "test_cases/7/01.a", "test_cases/7/02", "test_cases/7/02.a"],
        )
        self.assertEqual(result['uploaded'], 2)
        self.assertEqual(result['total'], 2)

    def test_stale_files_are_removed_after_a_successful_upload(self):
        storage = FakeStorage(existing=self.old_files(5))
        result = self.run_migration(storage, make_tests(3))
        self.assertEqual(len(storage.files), 6)
        self.assertNotIn("test_cases/7/04", storage.files)
        self.assertEqual(storage.files["test_cases/7/01"], "in1\n")
        self.assertEqual(result['stale_deleted'], 4)

    def test_files_of_other_problems_are_not_touched(self):
        storage = FakeStorage(existing={"test_cases/70/01": "other", "test_cases/8/01": "other"})
        self.run_migration(storage, make_tests(1))
        self.assertIn("test_cases/70/01", storage.files)
        self.assertIn("test_cases/8/01", storage.files)

    def test_failed_upload_deletes_nothing(self):
        storage = FakeStorage(existing=self.old_files(5), fail_on="test_cases/7/02.a")
        with self.assertRaises(RuntimeError):
            self.run_migration(storage, make_tests(3))
        # all ten old keys are still there: storage is never left empty
        self.assertEqual(len(storage.files), 10)

    def test_checker_failure_fails_the_migration_and_deletes_nothing(self):
        storage = FakeStorage(existing=self.old_files(5))
        with self.assertRaises(RuntimeError):
            self.run_migration(storage, make_tests(3), checker_error=RuntimeError("checker upload failed"))
        self.assertEqual(len(storage.files), 10)

    def test_checker_file_is_kept(self):
        storage = FakeStorage(existing={"test_cases/7/custom_checker": "binary"})
        checker = {'name': 'check.cpp', 'key': "test_cases/7/custom_checker", 'compiled': True}
        result = self.run_migration(storage, make_tests(1), checker=checker)
        self.assertIn("test_cases/7/custom_checker", storage.files)
        self.assertEqual(result['checker'], checker)

    def test_tests_without_input_or_output_are_reported_as_skipped(self):
        tests = make_tests(3)
        tests[1]['output'] = ''
        storage = FakeStorage()
        result = self.run_migration(storage, tests)
        self.assertEqual(result['skipped'], [2])
        self.assertEqual(result['uploaded'], 2)
        self.assertNotIn("test_cases/7/02", storage.files)

    def test_no_usable_test_raises_and_keeps_old_files(self):
        storage = FakeStorage(existing=self.old_files(2))
        tests = [{'index': 1, 'input': '', 'output': ''}]
        with self.assertRaises(RuntimeError):
            self.run_migration(storage, tests)
        self.assertEqual(len(storage.files), 4)


class UploadCustomCheckerTests(SimpleTestCase):
    """PolygonAPI.upload_custom_checker must not hide errors."""

    def test_no_custom_checker_returns_none(self):
        api = PolygonAPI()
        with mock.patch.object(api, 'get_custom_checker_info', return_value=None):
            self.assertIsNone(api.upload_custom_checker('999', FakeStorage(), 7))

    def test_missing_source_raises(self):
        api = PolygonAPI()
        with mock.patch.object(api, 'get_custom_checker_info', return_value={'name': 'check.cpp', 'type': 'custom'}), \
                mock.patch.object(api, 'fetch_custom_checker_file', return_value=None):
            with self.assertRaises(RuntimeError):
                api.upload_custom_checker('999', FakeStorage(), 7)

    def test_upload_error_raises(self):
        api = PolygonAPI()
        storage = FakeStorage(fail_on="test_cases/7/custom_checker.cpp")
        with mock.patch.object(api, 'get_custom_checker_info', return_value={'name': 'check.cpp', 'type': 'custom'}), \
                mock.patch.object(api, 'fetch_custom_checker_file', return_value="int main(){}"), \
                mock.patch.object(api, 'compile_custom_checker', return_value=None):
            with self.assertRaises(RuntimeError):
                api.upload_custom_checker('999', storage, 7)

    def test_source_is_uploaded_when_compilation_fails(self):
        api = PolygonAPI()
        storage = FakeStorage()
        with mock.patch.object(api, 'get_custom_checker_info', return_value={'name': 'check.cpp', 'type': 'custom'}), \
                mock.patch.object(api, 'fetch_custom_checker_file', return_value="int main(){}"), \
                mock.patch.object(api, 'compile_custom_checker', return_value=None):
            result = api.upload_custom_checker('999', storage, 7)
        self.assertEqual(result, {'name': 'check.cpp', 'key': "test_cases/7/custom_checker.cpp", 'compiled': False})
        self.assertIn("test_cases/7/custom_checker.cpp", storage.files)


class S3StorageTests(SimpleTestCase):
    def make_storage(self):
        with mock.patch('problems.storage.s3.boto3.client') as client_factory:
            storage = S3Storage(bucket='bucket')
        return storage, client_factory.return_value

    def set_listing(self, client, keys):
        client.get_paginator.return_value.paginate.return_value = [{'Contents': [{'Key': k} for k in keys]}]

    def test_missing_bucket_name_is_rejected(self):
        with self.assertRaises(ValueError):
            S3Storage(bucket='')

    def test_delete_raises_when_s3_reports_errors(self):
        storage, client = self.make_storage()
        self.set_listing(client, ["test_cases/7/01", "test_cases/7/01.a"])
        client.delete_objects.return_value = {
            'Deleted': [{'Key': "test_cases/7/01"}],
            'Errors': [{'Key': "test_cases/7/01.a", 'Code': 'AccessDenied', 'Message': 'Access Denied'}],
        }
        with self.assertRaises(RuntimeError) as raised:
            storage.delete_problem_files(7)
        self.assertIn('AccessDenied', str(raised.exception))

    def test_delete_skips_kept_keys(self):
        storage, client = self.make_storage()
        self.set_listing(client, ["test_cases/7/01", "test_cases/7/01.a", "test_cases/7/09"])
        client.delete_objects.return_value = {'Deleted': [{'Key': "test_cases/7/09"}]}
        deleted = storage.delete_problem_files(7, keep=["test_cases/7/01", "test_cases/7/01.a"])
        self.assertEqual(deleted, 1)
        client.delete_objects.assert_called_once_with(
            Bucket='bucket', Delete={'Objects': [{'Key': "test_cases/7/09"}]}
        )

    def test_upload_error_propagates(self):
        storage, client = self.make_storage()
        client.put_object.side_effect = RuntimeError("no access")
        with self.assertRaises(RuntimeError):
            storage.upload_test_case(7, 1, "in", "out")


class AzureStorageTests(SimpleTestCase):
    def make_storage(self):
        with mock.patch('problems.storage.azure.AzureBlobManager') as manager_class:
            storage = AzureStorage('https://account', 'tenant', 'client', 'user', 'password', 'container')
        return storage, manager_class.return_value.blob_service_client

    def test_upload_error_propagates_like_s3(self):
        storage, service = self.make_storage()
        service.get_blob_client.return_value.upload_blob.side_effect = RuntimeError("no access")
        with self.assertRaises(RuntimeError):
            storage.upload_test_case(7, 1, "in", "out")

    def test_upload_uses_the_required_layout(self):
        storage, service = self.make_storage()
        keys = storage.upload_test_case(7, 3, "in", "out")
        self.assertEqual(keys, ["test_cases/7/03", "test_cases/7/03.a"])

    def test_delete_error_propagates_and_kept_keys_are_skipped(self):
        storage, service = self.make_storage()
        container = service.get_container_client.return_value
        kept, stale = mock.Mock(), mock.Mock()
        kept.name, stale.name = "test_cases/7/01", "test_cases/7/09"
        container.list_blobs.return_value = [kept, stale]
        self.assertEqual(storage.delete_problem_files(7, keep=["test_cases/7/01"]), 1)
        container.delete_blob.assert_called_once_with("test_cases/7/09")

        container.delete_blob.side_effect = RuntimeError("no access")
        with self.assertRaises(RuntimeError):
            storage.delete_problem_files(7)
