"""FastAPI dependencies: database session, verified identity and the internal user.

Order of checks for protected routes: token present (401) -> token valid (401) -> user resolved (DB) ->
organization membership and role (404 or 403). The frontend never supplies the user identity.
"""

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser
from app.db.repositories import UserRepository
from app.db.session import Database
from app.security.tokens import AuthenticationError, AuthProviderUnavailableError, SupabaseTokenVerifier, VerifiedIdentity

_bearer = HTTPBearer(auto_error=False)


def get_database(request: Request) -> Database:
    database: Database | None = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return database


async def get_db_session(database: Database = Depends(get_database)) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session


async def get_verified_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> VerifiedIdentity:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(status_code=401, detail="AUTHENTICATION_REQUIRED", headers={"WWW-Authenticate": "Bearer"})

    verifier: SupabaseTokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        # Fail closed: without configuration, nothing that needs identity is served.
        raise HTTPException(status_code=503, detail="authentication not configured")
    try:
        return verifier.verify(credentials.credentials)
    except AuthenticationError:
        raise HTTPException(status_code=401, detail="INVALID_TOKEN", headers={"WWW-Authenticate": "Bearer"}) from None
    except AuthProviderUnavailableError:
        raise HTTPException(status_code=503, detail="authentication unavailable") from None


async def get_current_user(
    identity: VerifiedIdentity = Depends(get_verified_identity),
    session: AsyncSession = Depends(get_db_session),
) -> AppUser:
    # Creates only the local identity row. A first login grants no organization and no role.
    return await UserRepository(session).get_or_create_by_subject(
        external_subject=identity.subject, email=identity.email
    )
