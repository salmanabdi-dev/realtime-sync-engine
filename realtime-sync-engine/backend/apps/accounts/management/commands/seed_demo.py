"""Create demo users and a shared sample document (idempotent)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from apps.documents.models import Document, DocumentMembership, Role
from apps.documents.services import add_member, create_document

User = get_user_model()

DEMO_PASSWORD = "password"
DEMO_USERS = [("alice", Role.OWNER), ("bob", Role.EDITOR), ("carol", Role.VIEWER)]
SAMPLE_TITLE = "Welcome to the sync engine"
SAMPLE_CONTENT = (
    "Open this document in two browser windows and type in both.\n\n"
    "Every accepted change gets a new version number. If two people edit the same\n"
    "version at the same moment, one wins and the other rebases and retries.\n"
)


class Command(BaseCommand):
    help = "Seed demo users (alice/bob/carol, password 'password') and a sample document."

    def handle(self, *args, **options):
        users = {}
        for username, _role in DEMO_USERS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            users[username] = user
            Token.objects.get_or_create(user=user)

        doc = Document.objects.filter(title=SAMPLE_TITLE, created_by=users["alice"]).first()
        if doc is None:
            doc = create_document(users["alice"], SAMPLE_TITLE, SAMPLE_CONTENT)
        for username, role in DEMO_USERS:
            if not DocumentMembership.objects.filter(document=doc, user=users[username]).exists():
                add_member(doc, users[username], role)

        self.stdout.write(self.style.SUCCESS(f"Sample document: {doc.id}"))
        for username, role in DEMO_USERS:
            self.stdout.write(f"  {username:<6} role={role:<7} password={DEMO_PASSWORD}")
