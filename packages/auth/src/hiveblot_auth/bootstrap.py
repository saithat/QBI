"""One-time local/deployed identity and opaque bearer-token bootstrap CLI."""

from __future__ import annotations

import argparse
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

import psycopg
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .service import token_digest


class AuthBootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        strict=True,
    )

    database_url: str = Field(validation_alias="DATABASE_URL", min_length=1)
    auth_token_pepper: SecretStr = Field(
        validation_alias="AUTH_TOKEN_PEPPER",
        min_length=32,
    )


def main() -> None:
    args = _parser().parse_args()
    if (args.organization_slug is None) != (args.organization_name is None):
        raise SystemExit("--organization-slug and --organization-name must be provided together")
    settings = AuthBootstrapSettings()
    token = "hvb_" + secrets.token_urlsafe(32)
    created_at = datetime.now(UTC)
    user_id, organization_id, token_id = bootstrap_identity(
        settings.database_url,
        email=args.email,
        display_name=args.display_name,
        organization_slug=args.organization_slug,
        organization_name=args.organization_name,
        role=args.role,
        token_label=args.token_label,
        token=token,
        token_pepper=settings.auth_token_pepper.get_secret_value().encode("utf-8"),
        created_at=created_at,
        expires_at=created_at + timedelta(days=args.expires_days),
    )
    print(f"user_id={user_id}")
    if organization_id is not None:
        print(f"organization_id={organization_id}")
    print(f"token_id={token_id}")
    print(f"bearer_token={token}")
    print("Store the bearer token now; HiveBlot persists only its keyed digest.")


def bootstrap_identity(
    database_url: str,
    *,
    email: str,
    display_name: str,
    organization_slug: str | None,
    organization_name: str | None,
    role: str,
    token_label: str,
    token: str,
    token_pepper: bytes,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[UUID, UUID | None, UUID]:
    """Create or reuse identity records and issue a token whose plaintext is never stored."""

    normalized_email = email.strip().casefold()
    normalized_name = display_name.strip()
    if not normalized_email or not normalized_name:
        raise ValueError("email and display name cannot be blank")
    if expires_at <= created_at:
        raise ValueError("token expiration must follow creation")
    user_id = uuid4()
    organization_id: UUID | None = None
    token_id = uuid4()
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            SELECT user_id, user_status FROM auth_users WHERE lower(email) = %s FOR UPDATE
            """,
            (normalized_email,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO auth_users (
                    user_id, email, display_name, user_status, created_at, updated_at
                ) VALUES (%s, %s, %s, 'active', %s, %s)
                """,
                (user_id, normalized_email, normalized_name, created_at, created_at),
            )
        else:
            user_id = row[0]
            if row[1] != "active":
                raise ValueError("existing user is not active")

        if organization_slug is not None and organization_name is not None:
            organization_id = _ensure_organization(
                connection,
                slug=organization_slug,
                display_name=organization_name,
                user_id=user_id,
                role=role,
                created_at=created_at,
            )

        connection.execute(
            """
            INSERT INTO api_access_tokens (
                token_id, user_id, token_digest, label, created_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                token_id,
                user_id,
                token_digest(token, pepper=token_pepper),
                token_label.strip(),
                created_at,
                expires_at,
            ),
        )
    return user_id, organization_id, token_id


def _ensure_organization(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    slug: str,
    display_name: str,
    user_id: UUID,
    role: str,
    created_at: datetime,
) -> UUID:
    normalized_slug = slug.strip().casefold()
    row = connection.execute(
        """
        SELECT organization_id, organization_status
        FROM organizations WHERE slug = %s FOR UPDATE
        """,
        (normalized_slug,),
    ).fetchone()
    if row is None:
        organization_id = uuid4()
        connection.execute(
            """
            INSERT INTO organizations (
                organization_id, slug, display_name, organization_status, created_at, updated_at
            ) VALUES (%s, %s, %s, 'active', %s, %s)
            """,
            (
                organization_id,
                normalized_slug,
                display_name.strip(),
                created_at,
                created_at,
            ),
        )
    else:
        organization_id = cast(UUID, row[0])
        if row[1] != "active":
            raise ValueError("existing organization is not active")
    connection.execute(
        """
        INSERT INTO organization_memberships (
            membership_id, organization_id, user_id, role, active, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, TRUE, %s, %s)
        ON CONFLICT (organization_id, user_id) DO UPDATE SET
            role = EXCLUDED.role,
            active = TRUE,
            updated_at = EXCLUDED.updated_at
        """,
        (uuid4(), organization_id, user_id, role, created_at, created_at),
    )
    return organization_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a HiveBlot user and issue an opaque bearer token.",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--organization-slug")
    parser.add_argument("--organization-name")
    parser.add_argument(
        "--role",
        choices=("organization_administrator", "scientist", "reviewer", "read_only"),
        default="organization_administrator",
    )
    parser.add_argument("--token-label", default="bootstrap")
    parser.add_argument("--expires-days", type=_positive_integer, default=90)
    return parser


def _positive_integer(value: str) -> Annotated[int, Field(gt=0, le=3650)]:
    parsed = int(value)
    if not 1 <= parsed <= 3650:
        raise argparse.ArgumentTypeError("must be between 1 and 3650")
    return parsed


if __name__ == "__main__":  # pragma: no cover - console entry point
    main()
