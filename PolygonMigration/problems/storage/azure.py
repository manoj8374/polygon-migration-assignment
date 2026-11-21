"""Azure Blob Storage implementation (wraps the original AzureBlobManager)."""
import logging

from problems.AzureTestcase import AzureBlobManager

from .base import TestCaseStorage

logger = logging.getLogger(__name__)


class AzureStorage(TestCaseStorage):
    def __init__(self, account_url, tenant_id, client_id, username, password, container_name):
        self.container_name = container_name
        self._manager = AzureBlobManager(
            account_url=account_url,
            tenant_id=tenant_id,
            client_id=client_id,
            username=username,
            password=password,
        )

    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        self._manager.upload_test_case(
            self.container_name, problem_id, test_number, input_data, output_data
        )

    def delete_problem_files(self, problem_id):
        self._manager.empty_blob(self.container_name, problem_id)

    def upload_file(self, problem_id, filename, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        blob_client = self._manager.blob_service_client.get_blob_client(
            container=self.container_name, blob=self.file_key(problem_id, filename)
        )
        blob_client.upload_blob(data, overwrite=True)
