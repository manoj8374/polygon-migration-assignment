"""AWS S3 implementation (also works with S3-compatible services via endpoint_url)."""
import logging

import boto3

from .base import TestCaseStorage

logger = logging.getLogger(__name__)


class S3Storage(TestCaseStorage):
    def __init__(self, bucket, region=None, access_key_id=None,
                 secret_access_key=None, endpoint_url=None):
        if not bucket:
            raise ValueError("AWS_S3_BUCKET_NAME is not set")
        self.bucket = bucket
        # If keys are None, boto3 falls back to its default credential chain.
        self.client = boto3.client(
            "s3",
            region_name=region or None,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            endpoint_url=endpoint_url or None,
        )

    def _put(self, key, body):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
        logger.info("Uploaded s3://%s/%s", self.bucket, key)

    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        self._put(self.input_key(problem_id, test_number), input_data.encode("utf-8"))
        self._put(self.output_key(problem_id, test_number), output_data.encode("utf-8"))

    def upload_file(self, problem_id, filename, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._put(self.file_key(problem_id, filename), data)

    def delete_problem_files(self, problem_id):
        prefix = self.problem_prefix(problem_id)
        paginator = self.client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not objects:
                continue
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
            deleted += len(objects)
        logger.info("Deleted %d objects under s3://%s/%s", deleted, self.bucket, prefix)
