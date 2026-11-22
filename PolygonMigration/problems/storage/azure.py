"""
Azure Blob Storage implementation of TestCaseStorage.

AzureBlobManager (problems/AzureTestcase.py, from the starter code) is used only
to authenticate and build the BlobServiceClient. Its own upload_test_case and
empty_blob methods catch every exception and only log it, so this class talks to
the Azure SDK directly instead. That way Azure failures raise, exactly like S3.
"""
import logging

from problems.AzureTestcase import AzureBlobManager

from .base import TestCaseStorage

logger = logging.getLogger(__name__)


class AzureStorage(TestCaseStorage):
    def __init__(self, account_url, tenant_id, client_id, username, password, container_name):
        if not container_name:
            raise ValueError("AZURE_CONTAINER_NAME is not set")
        self.container_name = container_name
        self._manager = AzureBlobManager(
            account_url=account_url,
            tenant_id=tenant_id,
            client_id=client_id,
            username=username,
            password=password,
        )

    def _put(self, key, body):
        # upload_blob raises an azure.core exception on failure; we let it propagate.
        blob_client = self._manager.blob_service_client.get_blob_client(
            container=self.container_name, blob=key
        )
        blob_client.upload_blob(body, overwrite=True)
        logger.info("Uploaded blob %s/%s", self.container_name, key)
        return key

    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        return [
            self._put(self.input_key(problem_id, test_number), input_data.encode("utf-8")),
            self._put(self.output_key(problem_id, test_number), output_data.encode("utf-8")),
        ]

    def upload_file(self, problem_id, filename, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return self._put(self.file_key(problem_id, filename), data)

    def delete_problem_files(self, problem_id, keep=()):
        prefix = self.problem_prefix(problem_id)
        keep = set(keep)
        container_client = self._manager.blob_service_client.get_container_client(self.container_name)
        names = [b.name for b in container_client.list_blobs(name_starts_with=prefix) if b.name not in keep]
        for name in names:
            container_client.delete_blob(name)
        logger.info("Deleted %d blobs under %s/%s", len(names), self.container_name, prefix)
        return len(names)
