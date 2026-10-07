import time
import logging
from typing import Dict, Any, Optional
from config import settings

logger = logging.getLogger("GrowwAuth")
logger.setLevel(logging.INFO)

import base64
import hmac
import hashlib
import struct

def generate_totp_rfc6238(secret: str, interval: int = 30, digits: int = 6) -> str:
    """Pure-Python RFC 6238 TOTP generator without requiring external pyotp package."""
    clean_secret = secret.replace(" ", "").upper()
    missing_padding = len(clean_secret) % 8
    if missing_padding:
        clean_secret += "=" * (8 - missing_padding)
    key = base64.b32decode(clean_secret, casefold=True)
    counter = int(time.time() // interval)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)

class GrowwAuthManager:
    """
    Handles dynamic TOTP authentication and session token generation for Groww API.
    Provides automatic token caching and refreshed sessions.
    """
    def __init__(self):
        self.api_key = settings.GROWW_API_KEY
        self.api_secret = settings.GROWW_API_SECRET
        self.totp_secret = settings.GROWW_TOTP_SECRET
        self.client_id = settings.GROWW_CLIENT_ID
        self._access_token: Optional[str] = None
        self._token_expiry_timestamp: float = 0.0

    def generate_current_totp(self) -> str:
        """Generates a 6-digit TOTP code using the Base32 secret."""
        if not self.totp_secret:
            logger.warning("GROWW_TOTP_SECRET not provided in environment.")
            return "000000"
        
        try:
            return generate_totp_rfc6238(self.totp_secret)
        except Exception as e:
            logger.error(f"Error generating TOTP: {e}")
            return "000000"

    def get_session_token(self) -> str:
        """Return a cached Groww token in paper mode or a real token in GROWW live mode."""
        now = time.time()
        if self._access_token and (self._token_expiry_timestamp - now > 300):
            return self._access_token

        if settings.MARKET_DATA_MODE != "GROWW" or settings.DRY_RUN:
            logger.info("Using simulated Groww session only because MARKET_DATA_MODE is not GROWW or DRY_RUN is enabled.")
            self._access_token = f"SIMULATED_SESSION_TOKEN_{int(now)}"
            self._token_expiry_timestamp = now + 3600
            return self._access_token

        try:
            from groww_client import live_client
            self._access_token = live_client.authenticate()
            self._token_expiry_timestamp = live_client.access_token_expiry or (now + 20 * 3600)
            logger.info("Groww API session authenticated successfully.")
            return self._access_token
        except Exception as e:
            logger.error("Groww authentication failed: %s", e)
            self._access_token = None
            self._token_expiry_timestamp = 0.0
            raise RuntimeError("Groww LIVE_DATA authentication failed; refusing to fabricate a session token") from e

    def is_authenticated(self) -> bool:
        return bool(self._access_token and time.time() < self._token_expiry_timestamp)

auth_manager = GrowwAuthManager()
