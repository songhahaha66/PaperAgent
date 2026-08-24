from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.database import get_db
from models.models import Base, SystemConfig
from routers.auth_routes.auth import router as auth_router
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import CredentialDeviceType


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
app.include_router(auth_router)
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

ORIGIN = "http://localhost:5173"
HEADERS = {"Origin": ORIGIN}


def _session():
    return TestingSessionLocal()


def setup_module(_module):
    db = _session()
    if not db.query(SystemConfig).first():
        db.add(SystemConfig(is_allow_register=True))
        db.commit()
    db.close()


def _register_and_login(email: str, username: str, password: str = "secret12") -> str:
    register = client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": password},
    )
    assert register.status_code == 200, register.text
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def _verified_registration(credential_id: bytes = b"cred-id-1"):
    result = MagicMock()
    result.credential_id = credential_id
    result.credential_public_key = b"\x04public-key"
    result.sign_count = 0
    result.aaguid = "00000000-0000-0000-0000-000000000000"
    result.credential_device_type = CredentialDeviceType.MULTI_DEVICE
    result.credential_backed_up = True
    return result


def _verified_authentication(credential_id: bytes = b"cred-id-1"):
    result = MagicMock()
    result.credential_id = credential_id
    result.new_sign_count = 1
    result.credential_device_type = CredentialDeviceType.MULTI_DEVICE
    result.credential_backed_up = True
    result.user_verified = True
    return result


def test_register_options_requires_auth():
    response = client.post("/auth/passkey/register/options", headers=HEADERS)
    assert response.status_code == 401


def test_register_options_and_list_empty():
    token = _register_and_login("passkey-user@example.com", "passkey_user")
    options = client.post("/auth/passkey/register/options", headers=_auth_headers(token))
    assert options.status_code == 200, options.text
    body = options.json()
    assert body["challenge_id"]
    assert body["options"]["rp"]["id"] == "localhost"
    assert body["options"]["rp"]["name"] == "PaperAgent"
    assert body["options"]["user"]["name"] == "passkey-user@example.com"
    assert body["options"]["challenge"]
    assert body["options"]["authenticatorSelection"]["residentKey"] == "required"

    listed = client.get("/auth/passkey/credentials", headers=_auth_headers(token))
    assert listed.status_code == 200
    assert listed.json() == []


def test_register_verify_and_login_flow():
    token = _register_and_login("passkey-login@example.com", "passkey_login")
    options = client.post("/auth/passkey/register/options", headers=_auth_headers(token))
    challenge_id = options.json()["challenge_id"]
    credential_id = bytes_to_base64url(b"cred-id-login")
    credential = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {"clientDataJSON": "aaa", "attestationObject": "bbb", "transports": ["internal"]},
        "authenticatorAttachment": "platform",
        "clientExtensionResults": {},
    }

    with patch("auth.passkey.verify_registration_response", return_value=_verified_registration(b"cred-id-login")):
        saved = client.post(
            "/auth/passkey/register/verify",
            headers=_auth_headers(token),
            json={"challenge_id": challenge_id, "credential": credential, "name": "我的笔记本"},
        )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["name"] == "我的笔记本"
    assert saved_body["backed_up"] is True
    assert saved_body["transports"] == ["internal"]

    listed = client.get("/auth/passkey/credentials", headers=_auth_headers(token))
    assert len(listed.json()) == 1
    cred_pk = listed.json()[0]["id"]

    renamed = client.patch(
        f"/auth/passkey/credentials/{cred_pk}",
        headers=_auth_headers(token),
        json={"name": "办公室电脑"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "办公室电脑"

    login_options = client.post(
        "/auth/passkey/login/options",
        headers=HEADERS,
        json={"email": "passkey-login@example.com"},
    )
    assert login_options.status_code == 200, login_options.text
    login_challenge = login_options.json()["challenge_id"]
    assert login_options.json()["options"]["allowCredentials"]

    assertion = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": "aaa",
            "authenticatorData": "bbb",
            "signature": "ccc",
            "userHandle": "ddd",
        },
        "clientExtensionResults": {},
    }
    with patch(
        "auth.passkey.verify_authentication_response",
        return_value=_verified_authentication(b"cred-id-login"),
    ):
        logged_in = client.post(
            "/auth/passkey/login/verify",
            headers=HEADERS,
            json={"challenge_id": login_challenge, "credential": assertion},
        )
    assert logged_in.status_code == 200, logged_in.text
    passkey_token = logged_in.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {passkey_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "passkey-login@example.com"

    replay = client.post(
        "/auth/passkey/login/verify",
        headers=HEADERS,
        json={"challenge_id": login_challenge, "credential": assertion},
    )
    assert replay.status_code == 400


def test_usernameless_login_options_have_no_allow_credentials():
    response = client.post("/auth/passkey/login/options", headers=HEADERS, json={})
    assert response.status_code == 200, response.text
    options = response.json()["options"]
    assert "allowCredentials" not in options or options.get("allowCredentials") in (None, [])


def test_invalid_challenge_is_rejected():
    token = _register_and_login("challenge-user@example.com", "challenge_user")
    response = client.post(
        "/auth/passkey/register/verify",
        headers=_auth_headers(token),
        json={
            "challenge_id": "does-not-exist",
            "credential": {"id": "abc", "type": "public-key", "response": {}},
        },
    )
    assert response.status_code == 400


def test_expired_challenge_is_rejected():
    token = _register_and_login("expired-user@example.com", "expired_user")
    options = client.post("/auth/passkey/register/options", headers=_auth_headers(token))
    challenge_id = options.json()["challenge_id"]

    db = _session()
    from models import models

    record = (
        db.query(models.WebAuthnChallenge)
        .filter(models.WebAuthnChallenge.challenge_id == challenge_id)
        .first()
    )
    record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    db.close()

    response = client.post(
        "/auth/passkey/register/verify",
        headers=_auth_headers(token),
        json={
            "challenge_id": challenge_id,
            "credential": {"id": "abc", "type": "public-key", "response": {}},
        },
    )
    assert response.status_code == 400


def test_cannot_delete_another_users_passkey():
    owner_token = _register_and_login("owner@example.com", "owner_user")
    other_token = _register_and_login("other@example.com", "other_user")
    options = client.post("/auth/passkey/register/options", headers=_auth_headers(owner_token))
    challenge_id = options.json()["challenge_id"]
    credential_id = bytes_to_base64url(b"owner-cred")
    credential = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {"clientDataJSON": "a", "attestationObject": "b"},
        "clientExtensionResults": {},
    }
    with patch("auth.passkey.verify_registration_response", return_value=_verified_registration(b"owner-cred")):
        saved = client.post(
            "/auth/passkey/register/verify",
            headers=_auth_headers(owner_token),
            json={"challenge_id": challenge_id, "credential": credential},
        )
    cred_pk = saved.json()["id"]

    denied = client.delete(f"/auth/passkey/credentials/{cred_pk}", headers=_auth_headers(other_token))
    assert denied.status_code == 404

    deleted = client.delete(f"/auth/passkey/credentials/{cred_pk}", headers=_auth_headers(owner_token))
    assert deleted.status_code == 200
    listed = client.get("/auth/passkey/credentials", headers=_auth_headers(owner_token))
    assert listed.json() == []
