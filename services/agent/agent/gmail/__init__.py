"""Server-side Gmail: encrypted refresh-token handling, OAuth refresh/revoke, a read-only
value demo, and confirm-gated write actions (draft/send/modify).

Access tokens live in memory only; refresh tokens are AES-GCM ciphertext at rest
(`crypto.TokenCipher`, key from PERSONA_TOKEN_ENCRYPT_KEY). Nothing here logs token values.
"""
from .client import GmailClient  # noqa: F401
from .confirm import ConfirmGate, Confirmed, ConfirmationRequired  # noqa: F401
from .crypto import TokenCipher, TokenKeyError, generate_key  # noqa: F401
from .demo import value_demo  # noqa: F401
from .oauth import AccessToken, GmailError, GoogleOAuth, ReconnectRequired  # noqa: F401
from .service import GmailService  # noqa: F401
