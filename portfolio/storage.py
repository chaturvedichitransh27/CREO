"""Bounded database media storage for small, diskless hackathon deployments."""
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible
from urllib.parse import quote


@deconstructible
class DatabaseMediaStorage(Storage):
    def _model(self):
        from .models import StoredUpload
        return StoredUpload

    def _open(self, name, mode='rb'):
        if mode not in {'r', 'rb'}:
            raise ValueError('Stored uploads are read-only; save a new file instead.')
        try:
            row = self._model().objects.get(pk=name)
        except self._model().DoesNotExist:
            raise FileNotFoundError(name)
        return ContentFile(bytes(row.content), name=name)

    def _save(self, name, content):
        data = content.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError('Uploads must be at most 5 MB.')
        # Django's storage layer generates unused names; never overwrite a claim.
        self._model().objects.create(name=name, content=data, byte_size=len(data))
        return name

    def exists(self, name):
        return self._model().objects.filter(pk=name).exists()

    def delete(self, name):
        self._model().objects.filter(pk=name).delete()

    def size(self, name):
        try:
            return self._model().objects.values_list('byte_size', flat=True).get(pk=name)
        except self._model().DoesNotExist:
            raise FileNotFoundError(name)

    def url(self, name):
        if name.startswith(('evidence/', 'studio/')):
            raise ValueError('Private uploads have no public URL. Use their authenticated route.')
        return settings.MEDIA_URL + quote(name)
