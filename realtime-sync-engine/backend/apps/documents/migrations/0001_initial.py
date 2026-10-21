import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "documents", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="DocumentMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[("owner", "Owner"), ("editor", "Editor"), ("viewer", "Viewer")],
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="documents.document",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "document_memberships"},
        ),
        migrations.AddField(
            model_name="document",
            name="members",
            field=models.ManyToManyField(
                related_name="documents",
                through="documents.DocumentMembership",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="DocumentState",
            fields=[
                (
                    "document",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="state",
                        serialize=False,
                        to="documents.document",
                    ),
                ),
                ("content", models.TextField(blank=True, default="")),
                ("version", models.BigIntegerField(default=0)),
            ],
            options={"db_table": "document_states"},
        ),
        migrations.CreateModel(
            name="DocumentRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.BigIntegerField()),
                ("client_id", models.UUIDField()),
                ("mutation_id", models.UUIDField()),
                ("operations", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="documents.document",
                    ),
                ),
            ],
            options={"db_table": "document_revisions", "ordering": ["version"]},
        ),
        migrations.AddConstraint(
            model_name="documentmembership",
            constraint=models.UniqueConstraint(fields=("document", "user"), name="uniq_membership"),
        ),
        migrations.AddConstraint(
            model_name="documentmembership",
            constraint=models.CheckConstraint(
                condition=models.Q(("role__in", ["owner", "editor", "viewer"])),
                name="membership_role_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentstate",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gte", 0)), name="state_version_nonneg"
            ),
        ),
        migrations.AddConstraint(
            model_name="documentrevision",
            constraint=models.UniqueConstraint(fields=("document", "version"), name="uniq_revision_version"),
        ),
        migrations.AddConstraint(
            model_name="documentrevision",
            constraint=models.UniqueConstraint(
                fields=("document", "mutation_id"), name="uniq_revision_mutation"
            ),
        ),
        migrations.AddConstraint(
            model_name="documentrevision",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gt", 0)), name="revision_version_positive"
            ),
        ),
    ]
