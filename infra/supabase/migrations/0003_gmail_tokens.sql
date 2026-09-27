-- GMAIL-001: refresh-token lifecycle for gmail_connections.
-- `refresh_token_enc` (0001) holds AES-GCM ciphertext (agent/gmail/crypto.py); the key
-- lives in env (PERSONA_TOKEN_ENCRYPT_KEY), never in the DB. Access tokens are never stored.

alter table gmail_connections
  add column token_status    text not null default 'missing',  -- missing | stored | invalid | revoked | key_unavailable
  add column token_key_id    text,                             -- which app key encrypted refresh_token_enc
  add column last_refresh_at timestamptz,
  add column last_error      text,                             -- structured code only (e.g. invalid_grant), never token material
  add column revoked_at      timestamptz;
