"""Credential decryption.

Threat model, stated so the boundaries are auditable:

* Mongo holds Fernet ciphertext and nothing else. A database dump -- backup
  leak, read-only replica, compromised analytics account -- yields no usable
  credentials.
* The key lives only in this process's environment. The API can list and delete
  credential records but cannot read them, which means the largest and most
  exposed attack surface in the system (a public HTTP service) is not a path to
  a user's job-board password.
* Plaintext exists only inside `decrypted()` for the duration of one submission
  and is never written to a log, a trace, a screenshot, or a Mongo document.

What this does *not* protect against, and should be fixed before real users:
Fernet with a single env-var key gives no per-user isolation and makes rotation
a manual re-encryption pass over every document. `key_version` on
`BoardCredential` exists to support that migration. A KMS with per-user data
keys is the correct end state; this is an honest local-dev posture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import structlog
from cryptography.fernet import Fernet, InvalidToken

from worker.settings import get_settings

logger = structlog.get_logger(__name__)

_REDACTED: Final = "***"


class CredentialError(RuntimeError):
    """Credentials are unavailable or undecryptable.

    Deliberately carries no detail about *why* beyond a coarse category. This
    error can surface in a user-facing job result, and distinguishing "wrong
    key" from "corrupt ciphertext" tells an attacker which of the two they
    achieved.
    """


@dataclass(slots=True)
class BoardLogin:
    """Decrypted credentials for one job board.

    `__repr__` and `__str__` are overridden rather than relying on discipline.
    structlog serialises objects it is handed, `repr()` appears in tracebacks,
    and an f-string in a debug line added under time pressure is exactly how a
    password reaches a log aggregator. Making the safe behaviour the default
    means that mistake produces `***` instead of an incident.
    """

    username: str
    password: str
    totp_secret: str | None = None
    extra: dict[str, str] | None = None

    def __repr__(self) -> str:
        return f"BoardLogin(username={_REDACTED}, password={_REDACTED})"

    __str__ = __repr__

    def masked_username(self) -> str:
        """Safe-to-display form, e.g. `a***@gmail.com`."""
        if "@" in self.username:
            local, _, domain = self.username.partition("@")
            return f"{local[:1]}***@{domain}"
        return f"{self.username[:1]}***"


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise CredentialError(
            "No credential encryption key configured. This worker cannot submit to "
            "boards that require a login."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise CredentialError("Credential encryption key is malformed") from exc


def decrypt_login(encrypted_blob: str) -> BoardLogin:
    """Decrypt a `BoardCredential.encrypted_blob` into a `BoardLogin`."""
    import json

    try:
        plaintext = _fernet().decrypt(encrypted_blob.encode("utf-8"))
    except InvalidToken as exc:
        # Logged without the ciphertext: it is a valid oracle input and there is
        # no diagnostic value in retaining it.
        logger.error("credentials.decrypt_failed")
        raise CredentialError("Stored credentials could not be decrypted") from exc

    try:
        payload: dict[str, Any] = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise CredentialError("Decrypted credential payload is not valid JSON") from exc
    finally:
        # Best effort. CPython strings are immutable so this only drops the
        # reference; it shortens the window before GC rather than closing it.
        # Real zeroisation needs a bytearray, which is a change worth making if
        # this ever handles anything more sensitive than a job-board login.
        del plaintext

    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        raise CredentialError("Decrypted credential payload is missing required fields")

    return BoardLogin(
        username=username,
        password=password,
        totp_secret=payload.get("totp_secret"),
        extra={k: v for k, v in payload.items()
               if k not in {"username", "password", "totp_secret"}},
    )


def encrypt_login(login: BoardLogin) -> str:
    """Encrypt credentials for storage.

    Present for tests and for a future admin path. The API deliberately has no
    access to this function -- credentials are captured by a flow that runs
    here, not by an endpoint that would have to hold the key to work.
    """
    import json

    payload = {"username": login.username, "password": login.password}
    if login.totp_secret:
        payload["totp_secret"] = login.totp_secret
    if login.extra:
        payload.update(login.extra)
    return _fernet().encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")


def redact(text: str, secrets: list[str]) -> str:
    """Strip known secrets from text bound for a log or a stored document.

    Applied to error messages and scraped page text before persistence. A login
    failure page sometimes echoes the submitted username, and an unhandled
    exception from a fill() call can include the value it was filling.
    """
    out = text
    for secret in secrets:
        if secret and len(secret) > 3:
            out = out.replace(secret, _REDACTED)
    return out
