from django.db import models


class ProductSyncState(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    content_hash = models.CharField(max_length=64)
    last_synced_at = models.DateTimeField(auto_now=True)
    synced_as_new = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sku} (hash={self.content_hash[:8]}...)"
