import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateFileSystemStorage(FileSystemStorage):
    """File storage outside any web-served path. It deliberately has no public URL."""

    def __init__(self, **kwargs):
        super().__init__(location=str(settings.PRIVATE_MEDIA_ROOT), base_url=None, **kwargs)

    def url(self, name):
        raise ValueError("Private files have no public URL; use an authenticated download endpoint.")


def private_storage():
    return PrivateFileSystemStorage()


def upload_to(prefix):
    """Return an upload_to callable that stores files under an unguessable name.

    The user-supplied filename is never used on disk; it is kept in the database only.
    """

    def _upload(instance, filename):
        ext = ""
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[1].lower()[:8]
            if not ext[1:].isalnum():
                ext = ""
        return f"{prefix}/{uuid.uuid4().hex}{ext}"

    return _upload


class UploadTo:
    """Deconstructible upload_to so migrations can serialise it.

    A `{document_type}` placeholder in the prefix gives each document type its own folder,
    e.g. company/issued/invoice/<random>.pdf.
    """

    def __init__(self, prefix):
        self.prefix = prefix

    def __call__(self, instance, filename):
        folder = self.prefix.replace("{document_type}", str(getattr(instance, "document_type", "") or "other"))
        return upload_to(folder)(instance, filename)

    def deconstruct(self):
        return ("apps.core.storage.UploadTo", [self.prefix], {})

    def __eq__(self, other):
        return isinstance(other, UploadTo) and other.prefix == self.prefix
