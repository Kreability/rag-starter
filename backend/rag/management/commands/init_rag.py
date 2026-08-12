"""Provision the RAG infrastructure.

Creates the Qdrant collection (with payload indexes) and the S3 bucket, so a
fresh install is ready before the first request rather than lazily on first
upload. Idempotent — safe to run on every boot.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the Qdrant collection and object storage bucket if missing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wait",
            type=int,
            default=0,
            help="Seconds to keep retrying while dependencies come up.",
        )

    def handle(self, *args, **options):
        import time

        deadline = time.time() + options["wait"]

        for label, action in (
            ("object storage bucket", self._bucket),
            ("vector collection", self._collection),
        ):
            while True:
                try:
                    action()
                    self.stdout.write(self.style.SUCCESS(f"✓ {label} ready"))
                    break
                except Exception as exc:
                    if time.time() < deadline:
                        time.sleep(3)
                        continue
                    # Do not abort startup: the health endpoint reports this,
                    # and the API is still useful for auth and admin.
                    self.stdout.write(self.style.WARNING(f"✗ {label}: {exc}"))
                    break

    def _bucket(self):
        from rag.storage import ensure_bucket

        ensure_bucket()

    def _collection(self):
        from rag.vectordb import ensure_collection

        ensure_collection()
