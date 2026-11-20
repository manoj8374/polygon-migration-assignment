"""
Storage layer for test-case files.

Usage::

    from problems.storage import get_storage
    storage = get_storage()          # provider chosen by STORAGE_PROVIDER
    storage.upload_test_case(problem_id, 1, "in", "out")
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import TestCaseStorage


def get_storage():
    """Build the storage backend selected by the STORAGE_PROVIDER setting."""
    provider = (settings.STORAGE_PROVIDER or "azure").strip().lower()

    if provider == "s3":
        from .s3 import S3Storage
        return S3Storage(
            bucket=settings.AWS_S3_BUCKET_NAME,
            region=settings.AWS_REGION,
            access_key_id=settings.AWS_ACCESS_KEY_ID,
            secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        )

    if provider == "azure":
        from .azure import AzureStorage
        return AzureStorage(
            account_url=settings.AZURE_STORAGE_ACCOUNT_URL,
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            username=settings.AZURE_USERNAME,
            password=settings.AZURE_PASSWORD,
            container_name=settings.AZURE_CONTAINER_NAME,
        )

    raise ImproperlyConfigured(
        f"Unsupported STORAGE_PROVIDER '{provider}'. Supported: azure, s3."
    )


__all__ = ["TestCaseStorage", "get_storage"]
