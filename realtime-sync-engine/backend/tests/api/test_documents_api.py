import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def client_for(account=None):
    client = APIClient()
    if account:
        client.credentials(HTTP_AUTHORIZATION=f"Token {account.token}")
    return client


def test_register_and_login():
    c = APIClient()
    r = c.post("/api/auth/register/", {"username": "dana", "password": "secret"}, format="json")
    assert r.status_code == 201 and r.data["token"]
    assert c.post("/api/auth/register/", {"username": "dana", "password": "x1234"}, format="json").status_code == 409
    r = c.post("/api/auth/login/", {"username": "dana", "password": "secret"}, format="json")
    assert r.status_code == 200 and r.data["user"]["username"] == "dana"
    assert c.post("/api/auth/login/", {"username": "dana", "password": "wrong"}, format="json").status_code == 401


def test_create_list_and_open_document(accounts):
    c = client_for(accounts["alice"])
    r = c.post("/api/documents/", {"title": "Plan", "content": "v0"}, format="json")
    assert r.status_code == 201 and r.data["role"] == "owner" and r.data["version"] == 0
    doc_id = r.data["id"]
    assert [d["id"] for d in c.get("/api/documents/").data] == [doc_id]
    detail = c.get(f"/api/documents/{doc_id}/").data
    assert detail["content"] == "v0" and detail["members"][0]["username"] == "alice"


def test_non_member_gets_404(document, accounts):
    assert client_for(accounts["mallory"]).get(f"/api/documents/{document.id}/").status_code == 404


def test_owner_can_share_editor_cannot(document, accounts):
    r = client_for(accounts["alice"]).post(
        f"/api/documents/{document.id}/members/", {"username": "mallory", "role": "editor"}, format="json")
    assert r.status_code == 201
    assert client_for(accounts["mallory"]).get(f"/api/documents/{document.id}/").data["role"] == "editor"
    r = client_for(accounts["bob"]).post(
        f"/api/documents/{document.id}/members/", {"username": "carol", "role": "editor"}, format="json")
    assert r.status_code == 403


def test_requires_auth():
    assert client_for().get("/api/documents/").status_code == 401
