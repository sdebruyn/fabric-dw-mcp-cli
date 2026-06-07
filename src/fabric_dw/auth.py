from enum import StrEnum

from azure.core.credentials import AccessToken, TokenCredential

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
SQL_SCOPE = "https://database.windows.net/.default"


class CredentialMode(StrEnum):
    DEFAULT = "default"
    SERVICE_PRINCIPAL = "sp"
    INTERACTIVE = "interactive"


def get_credential(mode: CredentialMode = CredentialMode.DEFAULT) -> TokenCredential:
    raise NotImplementedError


async def get_token(credential: TokenCredential, scope: str) -> AccessToken:
    raise NotImplementedError
