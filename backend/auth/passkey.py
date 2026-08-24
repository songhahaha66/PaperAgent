"""Passkey / WebAuthn 注册与登录。"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes, options_to_json_dict
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
    WebAuthnException,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from models import models

CHALLENGE_TTL = timedelta(minutes=5)
DEFAULT_ORIGIN = "http://localhost:5173"
DEFAULT_RP_ID = "localhost"
DEFAULT_RP_NAME = "PaperAgent"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def user_handle_for_id(user_id: int) -> bytes:
    return user_id.to_bytes(8, "big")


def get_relying_party(request: Request) -> tuple[str, str, str]:
    """返回 (rp_id, rp_name, origin)。优先使用请求 Origin，可用环境变量覆盖。"""
    rp_name = os.getenv("WEBAUTHN_RP_NAME", DEFAULT_RP_NAME)
    origin_header = request.headers.get("origin")
    origin_env = os.getenv("WEBAUTHN_ORIGIN")
    origin = origin_header or origin_env or DEFAULT_ORIGIN

    allowed = [item.strip() for item in os.getenv("WEBAUTHN_ORIGINS", "").split(",") if item.strip()]
    if origin_env and origin_env not in allowed:
        allowed.append(origin_env)
    if allowed and origin not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid origin for passkey",
        )

    rp_id = os.getenv("WEBAUTHN_RP_ID")
    if not rp_id:
        hostname = urlparse(origin).hostname
        rp_id = hostname or DEFAULT_RP_ID
    return rp_id, rp_name, origin


def _cleanup_expired_challenges(db: Session) -> None:
    db.query(models.WebAuthnChallenge).filter(
        models.WebAuthnChallenge.expires_at < _utcnow()
    ).delete(synchronize_session=False)
    db.commit()


def _store_challenge(
    db: Session,
    *,
    challenge: bytes,
    challenge_type: str,
    user_id: Optional[int] = None,
) -> str:
    _cleanup_expired_challenges(db)
    challenge_id = secrets.token_urlsafe(32)
    record = models.WebAuthnChallenge(
        challenge_id=challenge_id,
        challenge=bytes_to_base64url(challenge),
        challenge_type=challenge_type,
        user_id=user_id,
        expires_at=_utcnow() + CHALLENGE_TTL,
    )
    db.add(record)
    db.commit()
    return challenge_id


def _consume_challenge(
    db: Session,
    challenge_id: str,
    expected_type: str,
) -> models.WebAuthnChallenge:
    record = (
        db.query(models.WebAuthnChallenge)
        .filter(models.WebAuthnChallenge.challenge_id == challenge_id)
        .first()
    )
    if not record or record.challenge_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired passkey challenge",
        )
    if _aware(record.expires_at) < _utcnow():
        db.delete(record)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired passkey challenge",
        )
    db.delete(record)
    db.commit()
    return record


def _parse_transports(raw) -> Optional[list[AuthenticatorTransport]]:
    if not raw:
        return None
    transports: list[AuthenticatorTransport] = []
    for item in raw:
        try:
            transports.append(AuthenticatorTransport(item))
        except ValueError:
            continue
    return transports or None


def _credential_descriptors(credentials: list[models.WebAuthnCredential]):
    descriptors = []
    for cred in credentials:
        descriptors.append(
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(cred.credential_id),
                transports=_parse_transports(cred.transports),
            )
        )
    return descriptors or None


def _default_passkey_name(credential: dict, fallback: str) -> str:
    attachment = credential.get("authenticatorAttachment")
    if attachment == "platform":
        return "本机 Passkey"
    if attachment == "cross-platform":
        return "安全密钥"
    return fallback


def create_registration_options(db: Session, user: models.User, request: Request) -> dict:
    rp_id, rp_name, _origin = get_relying_party(request)
    existing = (
        db.query(models.WebAuthnCredential)
        .filter(models.WebAuthnCredential.user_id == user.id)
        .all()
    )
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_handle_for_id(user.id),
        user_name=user.email,
        user_display_name=user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=_credential_descriptors(existing),
    )
    challenge_id = _store_challenge(
        db,
        challenge=options.challenge,
        challenge_type="registration",
        user_id=user.id,
    )
    return {
        "challenge_id": challenge_id,
        "options": options_to_json_dict(options),
    }


def verify_registration(
    db: Session,
    user: models.User,
    request: Request,
    challenge_id: str,
    credential: dict,
    name: Optional[str] = None,
) -> models.WebAuthnCredential:
    challenge = _consume_challenge(db, challenge_id, "registration")
    if challenge.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired passkey challenge",
        )

    rp_id, _rp_name, origin = get_relying_party(request)
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=False,
        )
    except (InvalidRegistrationResponse, WebAuthnException) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey registration failed",
        ) from exc

    credential_id = bytes_to_base64url(verification.credential_id)
    existing = (
        db.query(models.WebAuthnCredential)
        .filter(models.WebAuthnCredential.credential_id == credential_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey already registered",
        )

    trimmed_name = (name or "").strip()[:100]
    record = models.WebAuthnCredential(
        user_id=user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(verification.credential_public_key),
        sign_count=verification.sign_count,
        device_type=str(verification.credential_device_type.value)
        if hasattr(verification.credential_device_type, "value")
        else str(verification.credential_device_type),
        backed_up=bool(verification.credential_backed_up),
        transports=credential.get("response", {}).get("transports"),
        aaguid=verification.aaguid,
        name=trimmed_name or _default_passkey_name(credential, "Passkey"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def create_authentication_options(
    db: Session,
    request: Request,
    email: Optional[str] = None,
) -> dict:
    rp_id, _rp_name, _origin = get_relying_party(request)
    user_id = None
    allow_credentials = None
    if email:
        user = db.query(models.User).filter(models.User.email == email).first()
        if user:
            user_id = user.id
            creds = (
                db.query(models.WebAuthnCredential)
                .filter(models.WebAuthnCredential.user_id == user.id)
                .all()
            )
            allow_credentials = _credential_descriptors(creds)

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    challenge_id = _store_challenge(
        db,
        challenge=options.challenge,
        challenge_type="authentication",
        user_id=user_id,
    )
    return {
        "challenge_id": challenge_id,
        "options": options_to_json_dict(options),
    }


def verify_authentication(
    db: Session,
    request: Request,
    challenge_id: str,
    credential: dict,
) -> models.User:
    challenge = _consume_challenge(db, challenge_id, "authentication")
    credential_id = credential.get("id")
    if not credential_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey authentication failed",
        )

    stored = (
        db.query(models.WebAuthnCredential)
        .filter(models.WebAuthnCredential.credential_id == credential_id)
        .first()
    )
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown passkey",
        )
    if challenge.user_id is not None and stored.user_id != challenge.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey does not match this account",
        )

    rp_id, _rp_name, origin = get_relying_party(request)
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(stored.public_key),
            credential_current_sign_count=stored.sign_count,
            require_user_verification=False,
        )
    except (InvalidAuthenticationResponse, WebAuthnException) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey authentication failed",
        ) from exc

    stored.sign_count = verification.new_sign_count
    stored.backed_up = bool(verification.credential_backed_up)
    if verification.credential_device_type:
        stored.device_type = (
            verification.credential_device_type.value
            if hasattr(verification.credential_device_type, "value")
            else str(verification.credential_device_type)
        )
    stored.last_used_at = _utcnow()
    db.commit()

    user = db.query(models.User).filter(models.User.id == stored.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey authentication failed",
        )
    return user


def list_credentials(db: Session, user_id: int) -> list[models.WebAuthnCredential]:
    return (
        db.query(models.WebAuthnCredential)
        .filter(models.WebAuthnCredential.user_id == user_id)
        .order_by(models.WebAuthnCredential.created_at.desc())
        .all()
    )


def rename_credential(
    db: Session, user_id: int, credential_pk: int, name: str
) -> models.WebAuthnCredential:
    record = (
        db.query(models.WebAuthnCredential)
        .filter(
            models.WebAuthnCredential.id == credential_pk,
            models.WebAuthnCredential.user_id == user_id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    trimmed = name.strip()[:100]
    if not trimmed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey name is required")
    record.name = trimmed
    db.commit()
    db.refresh(record)
    return record


def delete_credential(db: Session, user_id: int, credential_pk: int) -> None:
    record = (
        db.query(models.WebAuthnCredential)
        .filter(
            models.WebAuthnCredential.id == credential_pk,
            models.WebAuthnCredential.user_id == user_id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    db.delete(record)
    db.commit()
