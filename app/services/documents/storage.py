import os
import tempfile
from contextlib import contextmanager
from mimetypes import guess_type
from pathlib import PurePosixPath
from urllib.parse import quote

from flask import current_app, redirect, send_file
from werkzeug.utils import secure_filename


class StorageError(RuntimeError):
    pass


class DocumentStorage:

    def save(self, file, storage_key):
        raise NotImplementedError

    def open(self, storage_key):
        raise NotImplementedError

    def exists(self, storage_key):
        raise NotImplementedError

    def delete(self, storage_key):
        raise NotImplementedError

    def get_download_response(
        self,
        storage_key,
        download_name=None,
        as_attachment=False,
    ):
        raise NotImplementedError

    def resolve_local_path(self, storage_key):
        return None

    @contextmanager
    def temporary_local_path(self, storage_key):
        local_path = self.resolve_local_path(storage_key)

        if local_path:
            yield local_path
            return

        suffix = os.path.splitext(storage_key.rsplit("/", 1)[-1])[1]
        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        )

        temp_path = temp_file.name

        try:
            with self.open(storage_key) as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_file.write(chunk)

            temp_file.close()
            yield temp_path

        finally:
            temp_file.close()

            if os.path.exists(temp_path):
                os.remove(temp_path)


class LocalStorage(DocumentStorage):

    def __init__(self, upload_folder):
        self.upload_folder = os.path.realpath(upload_folder)

    def save(self, file, storage_key):
        path = self.resolve_local_path(storage_key)
        if os.path.exists(path):
            raise StorageError("Document already exists in storage.")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        file.save(path)
        return storage_key

    @contextmanager
    def open(self, storage_key):
        path = self.resolve_local_path(storage_key)

        if not os.path.isfile(path):
            raise StorageError("Document file is not readable.")

        with open(path, "rb") as file:
            yield file

    def exists(self, storage_key):
        return os.path.isfile(self.resolve_local_path(storage_key))

    def delete(self, storage_key):
        path = self.resolve_local_path(storage_key)

        if os.path.isdir(path):
            raise StorageError("Document storage key points to a directory.")

        if os.path.isfile(path):
            os.remove(path)
            return True

        return False

    def get_download_response(
        self,
        storage_key,
        download_name=None,
        as_attachment=False,
    ):
        return send_file(
            self.resolve_local_path(storage_key),
            as_attachment=as_attachment,
            download_name=download_name,
        )

    def resolve_local_path(self, storage_key):
        key = normalize_storage_key(storage_key)
        path = os.path.realpath(
            os.path.join(
                self.upload_folder,
                *key.split("/"),
            )
        )

        if os.path.commonpath([self.upload_folder, path]) != self.upload_folder:
            raise ValueError("Invalid document storage key.")

        return path


class S3Storage(DocumentStorage):

    def __init__(
        self,
        bucket,
        region=None,
        endpoint_url=None,
        access_key_id=None,
        secret_access_key=None,
        public_base_url=None,
        presigned_url_ttl=300,
    ):
        self.bucket = bucket
        self.region = region
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.public_base_url = public_base_url
        self.presigned_url_ttl = presigned_url_ttl
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not self.bucket:
                raise StorageError("S3_BUCKET is required for S3 storage.")

            if (
                self.access_key_id
                and not self.secret_access_key
                or self.secret_access_key
                and not self.access_key_id
            ):
                raise StorageError(
                    "Both S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY are required."
                )

            try:
                import boto3
            except ImportError as exc:
                raise StorageError("boto3 is required for S3 storage.") from exc

            client_kwargs = {
                "region_name": self.region,
                "endpoint_url": self.endpoint_url or None,
            }

            if self.access_key_id and self.secret_access_key:
                client_kwargs.update(
                    {
                        "aws_access_key_id": self.access_key_id,
                        "aws_secret_access_key": self.secret_access_key,
                    }
                )

            self._client = boto3.client(
                "s3",
                **client_kwargs,
            )

        return self._client

    def save(self, file, storage_key):
        key = normalize_storage_key(storage_key)

        if self.exists(key):
            raise StorageError("Document already exists in storage.")

        file.stream.seek(0)
        content_type = (
            getattr(file, "mimetype", None)
            or guess_type(key)[0]
            or "application/octet-stream"
        )
        self.client.upload_fileobj(
            file.stream,
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
            },
        )
        return key

    @contextmanager
    def open(self, storage_key):
        key = normalize_storage_key(storage_key)
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=key,
            )
        except StorageError:
            raise

        except Exception as exc:
            raise StorageError("Unable to open document from storage.") from exc

        body = response["Body"]

        try:
            yield body
        finally:
            body.close()

    def exists(self, storage_key):
        key = normalize_storage_key(storage_key)

        try:
            self.client.head_object(
                Bucket=self.bucket,
                Key=key,
            )
            return True

        except StorageError:
            raise

        except Exception as exc:
            response = getattr(exc, "response", {})
            error = response.get("Error", {})
            code = str(error.get("Code", ""))

            if code in {"404", "NoSuchKey", "NotFound"}:
                return False

            raise StorageError("Unable to check document in storage.") from exc

    def delete(self, storage_key):
        key = normalize_storage_key(storage_key)

        try:
            self.client.delete_object(
                Bucket=self.bucket,
                Key=key,
            )
            return True

        except StorageError:
            raise

        except Exception as exc:
            raise StorageError("Unable to delete document from storage.") from exc

    def get_download_response(
        self,
        storage_key,
        download_name=None,
        as_attachment=False,
    ):
        key = normalize_storage_key(storage_key)
        params = {
            "Bucket": self.bucket,
            "Key": key,
        }

        if download_name:
            disposition = "attachment" if as_attachment else "inline"
            safe_download_name = secure_filename(download_name) or "document"
            params["ResponseContentDisposition"] = (
                f'{disposition}; filename="{safe_download_name}"'
            )

        if self.public_base_url:
            url = f"{self.public_base_url.rstrip('/')}/{quote(key)}"
        else:
            try:
                url = self.client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=self.presigned_url_ttl,
                )
            except StorageError:
                raise

            except Exception as exc:
                raise StorageError(
                    "Unable to create document download URL."
                ) from exc

        return redirect(url)


def normalize_storage_key(storage_key):
    if not storage_key:
        raise ValueError("Document storage key is required.")

    key = str(storage_key).strip()

    if "\\" in key:
        raise ValueError("Invalid document storage key.")

    if key.startswith("/") or "//" in key or ":" in key:
        raise ValueError("Invalid document storage key.")

    path = PurePosixPath(key)

    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Invalid document storage key.")

    return "/".join(path.parts)


def build_document_storage_key(filename, project_id=None, sub_id=None):
    safe_name = secure_filename(filename)

    if not safe_name:
        raise ValueError("Document filename is required.")

    max_filename_length = 180
    if len(safe_name) > max_filename_length:
        root, extension = os.path.splitext(safe_name)
        extension = extension[:20]
        safe_name = f"{root[:max_filename_length - len(extension)]}{extension}"

    if project_id is not None:
        project_id = _positive_int(project_id, "project_id")
        return normalize_storage_key(f"projects/{project_id}/{safe_name}")

    if sub_id is not None:
        sub_id = _positive_int(sub_id, "sub_id")
        return normalize_storage_key(f"subcontractors/{sub_id}/{safe_name}")

    return normalize_storage_key(safe_name)


def _positive_int(value, field_name):
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}.") from exc

    if integer <= 0:
        raise ValueError(f"Invalid {field_name}.")

    return integer


def get_document_storage():
    backend = str(current_app.config.get("STORAGE_BACKEND", "local")).strip().lower()

    if backend == "local":
        return LocalStorage(current_app.config["UPLOAD_FOLDER"])

    if backend == "s3":
        return S3Storage(
            bucket=current_app.config.get("S3_BUCKET"),
            region=current_app.config.get("S3_REGION"),
            endpoint_url=current_app.config.get("S3_ENDPOINT_URL"),
            access_key_id=current_app.config.get("S3_ACCESS_KEY_ID"),
            secret_access_key=current_app.config.get("S3_SECRET_ACCESS_KEY"),
            public_base_url=current_app.config.get("S3_PUBLIC_BASE_URL"),
            presigned_url_ttl=current_app.config.get("S3_PRESIGNED_URL_TTL", 300),
        )

    raise StorageError(f"Unsupported document storage backend: {backend}")


def document_storage_keys(doc):
    if not doc or not doc.filename:
        return []

    filename = str(doc.filename)
    basename = secure_filename(filename.rsplit("/", 1)[-1])
    candidates = []

    def add_candidate(value):
        try:
            key = normalize_storage_key(value)
        except ValueError:
            return

        if key not in candidates:
            candidates.append(key)

    if "/" in filename or "\\" in filename:
        add_candidate(filename)

    if doc.project_id and basename:
        add_candidate(f"projects/{doc.project_id}/{basename}")
        add_candidate(f"project_{doc.project_id}/{basename}")

    if doc.sub_id and basename:
        add_candidate(f"subcontractors/{doc.sub_id}/{basename}")

    if basename:
        add_candidate(basename)

    return candidates


def resolve_document_storage_key(doc):
    storage = get_document_storage()
    candidates = document_storage_keys(doc)

    for key in candidates:
        if storage.exists(key):
            return key

    return candidates[0] if candidates else None


def document_exists(doc):
    key = resolve_document_storage_key(doc)
    return bool(key and get_document_storage().exists(key))


def resolve_document_path(doc):
    key = resolve_document_storage_key(doc)

    if not key:
        return None

    return get_document_storage().resolve_local_path(key)


def document_send_directory(doc):
    path = resolve_document_path(doc)

    if not path:
        return None, None

    return os.path.dirname(path), os.path.basename(path)


def get_document_response(doc, as_attachment=False):
    key = resolve_document_storage_key(doc)

    if not key or not get_document_storage().exists(key):
        return None

    return get_document_storage().get_download_response(
        key,
        download_name=doc.original_name or key.rsplit("/", 1)[-1],
        as_attachment=as_attachment,
    )


@contextmanager
def temporary_document_path(doc):
    key = resolve_document_storage_key(doc)

    if not key or not get_document_storage().exists(key):
        yield None
        return

    with get_document_storage().temporary_local_path(key) as path:
        yield path


def save_document_file(file, filename, project_id=None, sub_id=None):
    storage_key = build_document_storage_key(
        filename,
        project_id=project_id,
        sub_id=sub_id,
    )
    get_document_storage().save(file, storage_key)
    return storage_key


def delete_document_file(doc):
    key = resolve_document_storage_key(doc)

    if not key or _has_other_reference(doc):
        return False

    return get_document_storage().delete(key)


def cleanup_saved_document(storage_key):
    if not storage_key:
        return False

    try:
        return get_document_storage().delete(storage_key)
    except Exception:
        current_app.logger.exception(
            "Document storage cleanup failed for key=%s",
            storage_key,
        )
        return False


def _has_other_reference(doc):
    from app.models import Document

    if not doc or not doc.filename:
        return False

    return (
        Document.query
        .filter(
            Document.id != doc.id,
            Document.filename == doc.filename,
        )
        .first()
        is not None
    )
