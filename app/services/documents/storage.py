import os

from flask import current_app


def _upload_root():
    return current_app.config["UPLOAD_FOLDER"]


def _safe_path(*parts):
    root = os.path.abspath(_upload_root())
    path = os.path.abspath(os.path.join(root, *parts))

    if os.path.commonpath([root, path]) != root:
        raise ValueError("Invalid document path.")

    return path


def project_document_folder(project_id):
    return _safe_path(f"project_{project_id}")


def document_storage_folder(doc):
    if doc.project_id:
        return project_document_folder(doc.project_id)

    return _upload_root()


def resolve_document_path(doc):
    """Return the physical path for a document, including legacy locations."""
    if not doc or not doc.filename:
        return None

    candidate_paths = []

    if doc.project_id:
        candidate_paths.append(
            _safe_path(f"project_{doc.project_id}", doc.filename)
        )

    candidate_paths.append(
        _safe_path(doc.filename)
    )

    for path in candidate_paths:
        if os.path.exists(path):
            return path

    return candidate_paths[0]


def document_send_directory(doc):
    path = resolve_document_path(doc)

    if not path:
        return None, None

    return os.path.dirname(path), os.path.basename(path)


def save_document_file(file, filename, project_id=None):
    if project_id:
        folder = project_document_folder(project_id)
    else:
        folder = _upload_root()

    os.makedirs(folder, exist_ok=True)

    path = _safe_path(
        f"project_{project_id}",
        filename,
    ) if project_id else _safe_path(filename)

    file.save(path)

    return path


def delete_document_file(doc):
    path = resolve_document_path(doc)

    if path and os.path.exists(path) and not _has_other_reference(doc):
        os.remove(path)
        return True

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
