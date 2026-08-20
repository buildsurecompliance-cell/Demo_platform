import os
import re
import tempfile
import types
import unittest

from io import BytesIO
from unittest.mock import ANY, Mock, patch

from werkzeug.datastructures import FileStorage

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document
from app.services.documents.storage import (
    LocalStorage,
    S3Storage,
    StorageError,
    build_document_storage_key,
    cleanup_saved_document,
    document_storage_keys,
    get_document_storage,
    normalize_storage_key,
    resolve_document_storage_key,
    save_document_file,
    temporary_document_path,
)


class FakeClientError(Exception):

    def __init__(self, code):
        self.response = {
            "Error": {
                "Code": code,
            }
        }


class FakeBody(BytesIO):

    def __init__(self, content):
        super().__init__(content)
        self.closed_by_storage = False

    def close(self):
        self.closed_by_storage = True
        super().close()


class DocumentStorageTest(unittest.TestCase):

    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        upload_folder = self.uploads.name

        class LocalStorageTestingConfig(TestingConfig):
            UPLOAD_FOLDER = upload_folder
            STORAGE_BACKEND = "local"

        self.app = create_app(LocalStorageTestingConfig)

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.uploads.cleanup()

    def file(self, content=b"document", filename="document.pdf"):
        return FileStorage(
            stream=BytesIO(content),
            filename=filename,
        )

    def test_local_storage_save_exists_open_and_delete(self):
        storage = LocalStorage(self.uploads.name)
        key = "subcontractors/10/example.pdf"

        storage.save(self.file(b"hello"), key)

        self.assertTrue(storage.exists(key))

        with storage.open(key) as stored_file:
            self.assertEqual(stored_file.read(), b"hello")

        self.assertTrue(storage.delete(key))
        self.assertFalse(storage.exists(key))
        self.assertFalse(storage.delete(key))

    def test_local_storage_blocks_path_traversal(self):
        storage = LocalStorage(self.uploads.name)

        with self.assertRaises(ValueError):
            storage.resolve_local_path("../secret.pdf")

        with self.assertRaises(ValueError):
            normalize_storage_key("C:/secret.pdf")

        with self.assertRaises(ValueError):
            normalize_storage_key(r"..\secret.pdf")

        with self.assertRaises(ValueError):
            normalize_storage_key("/absolute.pdf")

        with self.assertRaises(ValueError):
            normalize_storage_key("projects//file.pdf")

        with self.assertRaises(ValueError):
            normalize_storage_key("")

    def test_local_storage_blocks_symlink_escape_when_supported(self):
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.close()
        link_path = os.path.join(self.uploads.name, "escape.pdf")

        try:
            os.symlink(outside.name, link_path)
        except (AttributeError, NotImplementedError, OSError):
            os.unlink(outside.name)
            self.skipTest("Symlink creation is not supported in this environment.")

        storage = LocalStorage(self.uploads.name)

        try:
            with self.assertRaises(ValueError):
                storage.resolve_local_path("escape.pdf")
        finally:
            os.unlink(link_path)
            os.unlink(outside.name)

    def test_local_storage_rejects_directory_open_and_delete(self):
        storage = LocalStorage(self.uploads.name)
        os.makedirs(os.path.join(self.uploads.name, "projects"), exist_ok=True)

        with self.assertRaises(StorageError):
            with storage.open("projects"):
                pass

        with self.assertRaises(StorageError):
            storage.delete("projects")

    def test_local_storage_does_not_overwrite_existing_key(self):
        storage = LocalStorage(self.uploads.name)
        storage.save(self.file(b"first"), "projects/1/same.pdf")

        with self.assertRaises(StorageError):
            storage.save(self.file(b"second"), "projects/1/same.pdf")

        with storage.open("projects/1/same.pdf") as stored_file:
            self.assertEqual(stored_file.read(), b"first")

    def test_storage_key_uses_project_and_subcontractor_prefixes(self):
        self.assertRegex(
            build_document_storage_key("Contract.pdf", project_id=3),
            r"^projects/3/[a-f0-9]{32}\.pdf$",
        )
        self.assertRegex(
            build_document_storage_key("COI.pdf", sub_id=9),
            r"^subcontractors/9/[a-f0-9]{32}\.pdf$",
        )

    def test_storage_key_sanitizes_unusual_filenames(self):
        cases = [
            ("../unsafe.pdf", "unsafe.pdf"),
            (r"..\unsafe.pdf", "unsafe.pdf"),
            ("COI final 2026.pdf", "COI_final_2026.pdf"),
            ("café scope.pdf", "cafe_scope.pdf"),
            ("archive.tar.pdf", "archive.tar.pdf"),
            ("nested/path/file.pdf", "nested_path_file.pdf"),
        ]

        for raw_name, expected_name in cases:
            with self.subTest(raw_name=raw_name):
                self.assertRegex(
                    build_document_storage_key(raw_name, project_id=1),
                    r"^projects/1/[a-f0-9]{32}\.pdf$",
                )

    def test_storage_key_rejects_empty_or_invalid_filename(self):
        for raw_name in ["", "...", "///"]:
            with self.subTest(raw_name=raw_name):
                with self.assertRaises(ValueError):
                    build_document_storage_key(raw_name, project_id=1)

    def test_storage_key_rejects_invalid_ids(self):
        for project_id in [0, -1, "abc"]:
            with self.subTest(project_id=project_id):
                with self.assertRaises(ValueError):
                    build_document_storage_key("file.pdf", project_id=project_id)

        for sub_id in [0, -1, "abc"]:
            with self.subTest(sub_id=sub_id):
                with self.assertRaises(ValueError):
                    build_document_storage_key("file.pdf", sub_id=sub_id)

    def test_storage_key_truncates_long_filename_but_preserves_extension(self):
        storage_key = build_document_storage_key(
            f"{'a' * 260}.pdf",
            project_id=1,
        )

        filename = storage_key.rsplit("/", 1)[-1]
        self.assertRegex(filename, r"^[a-f0-9]{32}\.pdf$")

    def test_unknown_backend_fails_clearly(self):
        self.app.config["STORAGE_BACKEND"] = "mystery"

        with self.assertRaisesRegex(StorageError, "Unsupported"):
            get_document_storage()

    def test_document_legacy_project_path_is_found(self):
        doc = Document(
            filename="legacy.pdf",
            original_name="legacy.pdf",
            document_type="Contract",
            project_id=7,
        )
        storage = Mock()
        storage.exists.side_effect = lambda key: key == "project_7/legacy.pdf"

        self.assertIn("project_7/legacy.pdf", document_storage_keys(doc))
        with patch(
            "app.services.documents.storage.get_document_storage",
            return_value=storage,
        ):
            with patch.object(self.app.logger, "warning") as warning:
                self.assertEqual(
                    resolve_document_storage_key(doc),
                    "project_7/legacy.pdf",
                )

        warning.assert_called_once()
        self.assertIn("legacy storage fallback", warning.call_args.args[0])

    def test_exact_filename_is_preferred_over_legacy_fallback(self):
        os.makedirs(
            os.path.join(self.uploads.name, "subcontractors", "4"),
            exist_ok=True,
        )
        with open(
            os.path.join(self.uploads.name, "subcontractors", "4", "current.pdf"),
            "wb",
        ) as file:
            file.write(b"current")
        with open(os.path.join(self.uploads.name, "current.pdf"), "wb") as file:
            file.write(b"legacy")

        doc = Document(
            filename="subcontractors/4/current.pdf",
            original_name="current.pdf",
            document_type="COI",
            sub_id=4,
        )

        self.assertEqual(
            resolve_document_storage_key(doc),
            "subcontractors/4/current.pdf",
        )

    def test_document_legacy_root_path_is_found(self):
        with open(os.path.join(self.uploads.name, "legacy.pdf"), "wb") as file:
            file.write(b"legacy")

        doc = Document(
            filename="legacy.pdf",
            original_name="legacy.pdf",
            document_type="COI",
            sub_id=4,
        )

        self.assertEqual(resolve_document_storage_key(doc), "legacy.pdf")

    def test_resolution_prefers_exact_filename_before_legacy_candidates(self):
        os.makedirs(
            os.path.join(self.uploads.name, "projects", "7"),
            exist_ok=True,
        )
        os.makedirs(
            os.path.join(self.uploads.name, "project_7"),
            exist_ok=True,
        )

        with open(
            os.path.join(self.uploads.name, "projects", "7", "same.pdf"),
            "wb",
        ) as file:
            file.write(b"new")

        with open(
            os.path.join(self.uploads.name, "project_7", "same.pdf"),
            "wb",
        ) as file:
            file.write(b"legacy-project")

        with open(os.path.join(self.uploads.name, "same.pdf"), "wb") as file:
            file.write(b"legacy-root")

        project_doc = Document(
            filename="projects/7/same.pdf",
            original_name="same.pdf",
            document_type="Contract",
            project_id=7,
        )
        sub_doc = Document(
            filename="same.pdf",
            original_name="same.pdf",
            document_type="COI",
            sub_id=7,
        )

        self.assertEqual(
            resolve_document_storage_key(project_doc),
            "projects/7/same.pdf",
        )
        self.assertEqual(resolve_document_storage_key(sub_doc), "same.pdf")

    def test_save_document_file_returns_unique_project_key(self):
        storage_key = save_document_file(
            self.file(b"project"),
            "abc_contract.pdf",
            project_id=5,
        )
        second_key = save_document_file(
            self.file(b"other"),
            "abc_contract.pdf",
            project_id=5,
        )

        self.assertNotEqual(storage_key, second_key)
        self.assertRegex(storage_key, r"^projects/5/[a-f0-9]{32}\.pdf$")
        self.assertRegex(second_key, r"^projects/5/[a-f0-9]{32}\.pdf$")
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.uploads.name,
                    *storage_key.split("/"),
                )
            )
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.uploads.name,
                    *second_key.split("/"),
                )
            )
        )

    def test_cleanup_saved_document_removes_physical_file(self):
        storage_key = save_document_file(
            self.file(b"temporary"),
            "temp.pdf",
            sub_id=2,
        )

        self.assertTrue(cleanup_saved_document(storage_key))
        self.assertFalse(LocalStorage(self.uploads.name).exists(storage_key))

    def test_cleanup_saved_document_logs_failure_without_raising(self):
        storage_factory = Mock()
        storage_factory.return_value.delete.side_effect = StorageError(
            "cleanup failed"
        )

        with patch.dict(
            cleanup_saved_document.__globals__,
            {"get_document_storage": storage_factory},
        ), patch.object(self.app.logger, "exception") as logger_exception:
            result = cleanup_saved_document("projects/1/file.pdf")

        self.assertFalse(result)
        logger_exception.assert_called_once()

    def test_temporary_document_path_uses_local_file_without_copy(self):
        storage_key = save_document_file(
            self.file(b"analysis"),
            "coi.pdf",
            sub_id=2,
        )
        doc = Document(
            filename=storage_key,
            original_name="coi.pdf",
            document_type="COI",
            sub_id=2,
        )

        with temporary_document_path(doc) as path:
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as file:
                self.assertEqual(file.read(), b"analysis")

        self.assertTrue(os.path.exists(path))

    def test_s3_storage_does_not_create_client_until_used(self):
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )

        self.assertIsNone(storage._client)

    def test_s3_storage_requires_bucket_when_used(self):
        storage = S3Storage(bucket=None)

        with self.assertRaisesRegex(StorageError, "S3_BUCKET"):
            storage.exists("documents/file.pdf")

    def test_s3_storage_allows_provider_chain_without_explicit_credentials(self):
        fake_boto3 = types.SimpleNamespace(client=Mock())

        with patch.dict("sys.modules", {"boto3": fake_boto3}):
            storage = S3Storage(bucket="bucket")
            storage.client

        fake_boto3.client.assert_called_once_with(
            "s3",
            region_name=None,
            endpoint_url=None,
        )

    def test_s3_storage_rejects_partial_credentials_when_used(self):
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
        )

        with self.assertRaisesRegex(StorageError, "Both S3_ACCESS_KEY_ID"):
            storage.exists("documents/file.pdf")

    def test_s3_storage_uses_lazy_boto3_client(self):
        fake_boto3 = types.SimpleNamespace(client=Mock())

        with patch.dict("sys.modules", {"boto3": fake_boto3}):
            storage = S3Storage(
                bucket="bucket",
                region="us-east-1",
                endpoint_url="https://example.invalid",
                access_key_id="key",
                secret_access_key="secret",
            )
            storage._client = None
            storage.client

        fake_boto3.client.assert_called_once_with(
            "s3",
            region_name="us-east-1",
            endpoint_url="https://example.invalid",
            aws_access_key_id="key",
            aws_secret_access_key="secret",
        )

    def test_s3_storage_upload_exists_delete_and_presigned_url(self):
        client = Mock()
        client.head_object.side_effect = [
            FakeClientError("404"),
            {},
        ]
        client.generate_presigned_url.return_value = "https://signed.example/doc"
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )
        storage._client = client

        storage.save(self.file(b"abc"), "projects/1/a.pdf")
        self.assertTrue(storage.exists("projects/1/a.pdf"))
        self.assertTrue(storage.delete("projects/1/a.pdf"))

        with self.app.test_request_context():
            response = storage.get_download_response(
                "projects/1/a.pdf",
                download_name="a.pdf",
                as_attachment=True,
            )

        client.upload_fileobj.assert_called_once_with(
            ANY,
            "bucket",
            "projects/1/a.pdf",
            ExtraArgs={
                "ContentType": "application/pdf",
            },
        )
        self.assertEqual(client.head_object.call_count, 2)
        client.delete_object.assert_called_once_with(
            Bucket="bucket",
            Key="projects/1/a.pdf",
        )
        client.generate_presigned_url.assert_called_once()
        self.assertEqual(
            client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            300,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://signed.example/doc")

    def test_s3_presigned_url_uses_inline_disposition_for_view(self):
        client = Mock()
        client.generate_presigned_url.return_value = "https://signed.example/view"
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
            presigned_url_ttl=120,
        )
        storage._client = client

        with self.app.test_request_context():
            response = storage.get_download_response(
                "projects/1/a.pdf",
                download_name="unsafe name.pdf",
                as_attachment=False,
            )

        params = client.generate_presigned_url.call_args.kwargs["Params"]
        self.assertEqual(
            params["ResponseContentDisposition"],
            'inline; filename="unsafe_name.pdf"',
        )
        self.assertEqual(
            client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            120,
        )
        self.assertEqual(response.status_code, 302)

    def test_s3_public_base_url_is_explicit_redirect_without_credentials(self):
        storage = S3Storage(
            bucket="bucket",
            public_base_url="https://cdn.example/documents",
        )

        with self.app.test_request_context():
            response = storage.get_download_response(
                "projects/1/a file.pdf",
                download_name="a file.pdf",
                as_attachment=True,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.location,
            "https://cdn.example/documents/projects/1/a%20file.pdf",
        )

    def test_s3_storage_does_not_overwrite_existing_key(self):
        client = Mock()
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )
        storage._client = client

        with self.assertRaises(StorageError):
            storage.save(self.file(b"abc"), "projects/1/a.pdf")

        client.upload_fileobj.assert_not_called()

    def test_s3_storage_exists_returns_false_for_missing_key(self):
        client = Mock()
        client.head_object.side_effect = FakeClientError("404")
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )
        storage._client = client

        self.assertFalse(storage.exists("missing.pdf"))

    def test_s3_storage_exists_raises_for_bucket_error(self):
        client = Mock()
        client.head_object.side_effect = FakeClientError("AccessDenied")
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )
        storage._client = client

        with self.assertRaises(StorageError):
            storage.exists("missing.pdf")

    def test_s3_storage_delete_raises_for_permission_error(self):
        client = Mock()
        client.delete_object.side_effect = FakeClientError("AccessDenied")
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )
        storage._client = client

        with self.assertRaises(StorageError):
            storage.delete("missing.pdf")

    def test_s3_storage_rejects_unsafe_keys(self):
        storage = S3Storage(
            bucket="bucket",
            access_key_id="key",
            secret_access_key="secret",
        )

        with self.assertRaises(ValueError):
            storage.exists("../secret.pdf")

        with self.assertRaises(ValueError):
            storage.exists(r"..\secret.pdf")

    def test_s3_temporary_document_path_downloads_and_cleans_temp_file(self):
        client = Mock()
        client.head_object.return_value = {}
        body = FakeBody(b"downloaded")
        client.get_object.return_value = {
            "Body": body,
        }

        doc = Document(
            filename="subcontractors/2/coi.pdf",
            original_name="coi.pdf",
            document_type="COI",
            sub_id=2,
        )

        with patch(
            "app.services.documents.storage.get_document_storage",
            return_value=S3Storage(
                bucket="bucket",
                access_key_id="key",
                secret_access_key="secret",
            ),
        ) as storage_factory:
            storage_factory.return_value._client = client

            with temporary_document_path(doc) as path:
                self.assertTrue(os.path.exists(path))
                with open(path, "rb") as file:
                    self.assertEqual(file.read(), b"downloaded")

            self.assertFalse(os.path.exists(path))
            self.assertTrue(body.closed_by_storage)

    def test_s3_temporary_document_path_cleans_temp_file_on_analysis_error(self):
        client = Mock()
        client.head_object.return_value = {}
        client.get_object.return_value = {
            "Body": BytesIO(b"downloaded"),
        }

        doc = Document(
            filename="subcontractors/2/coi.pdf",
            original_name="coi.pdf",
            document_type="COI",
            sub_id=2,
        )

        with patch(
            "app.services.documents.storage.get_document_storage",
            return_value=S3Storage(
                bucket="bucket",
                access_key_id="key",
                secret_access_key="secret",
            ),
        ) as storage_factory:
            storage_factory.return_value._client = client

            with self.assertRaises(RuntimeError):
                with temporary_document_path(doc) as path:
                    self.assertTrue(os.path.exists(path))
                    raise RuntimeError("analysis failed")

            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
