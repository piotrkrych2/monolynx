# MCP OAuth 2.1 Blueprint — instrukcja dla Claude Code

Instrukcja implementacji serwera autoryzacji OAuth 2.1 dla MCP (Model Context Protocol), kompatybilnego z Claude Desktop i Claude Code. Skopiuj ten plik do innego projektu i poproś Claude Code o implementację.

## Co to robi?

Po implementacji użytkownicy będą mogli:
1. W Claude Desktop kliknąć "Connect" przy Twoim MCP serverze
2. Zalogować się na Twojej platformie (email + hasło)
3. Zatwierdzić dostęp (consent screen)
4. Claude Desktop automatycznie otrzyma token i będzie mógł wywoływać narzędzia MCP

Działa to dzięki standardowi OAuth 2.1 z PKCE — Claude Desktop sam obsługuje flow kliencki.

---

## Wymagane endpointy

### 1. Metadata — discovery (RFC 8414 + RFC 9728)

Claude Desktop najpierw odpytuje te endpointy, żeby odkryć resztę:

#### `GET /.well-known/oauth-protected-resource`

```json
{
  "resource": "https://twoja-aplikacja.com",
  "authorization_servers": ["https://twoja-aplikacja.com"],
  "bearer_methods_supported": ["header"]
}
```

#### `GET /.well-known/oauth-authorization-server`

```json
{
  "issuer": "https://twoja-aplikacja.com",
  "authorization_endpoint": "https://twoja-aplikacja.com/authorize",
  "token_endpoint": "https://twoja-aplikacja.com/token",
  "registration_endpoint": "https://twoja-aplikacja.com/register",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"]
}
```

### 2. Dynamic Client Registration (RFC 7591)

#### `POST /register`

Claude Desktop sam się rejestruje jako klient OAuth.

**Request** (JSON):
```json
{
  "client_name": "Claude Desktop",
  "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
  "grant_types": ["authorization_code", "refresh_token"]
}
```

**Response** (201):
```json
{
  "client_id": "mlx_abc123...",
  "client_name": "Claude Desktop",
  "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
  "grant_types": ["authorization_code", "refresh_token"]
}
```

**Implementacja:**
- Wygeneruj unikalny `client_id` (np. `prefix_ + secrets.token_urlsafe(32)`)
- **NIE generuj `client_secret`** — to public client (bez sekretu)
- Zapisz w DB: `client_id`, `client_name`, `redirect_uris` (JSON array), `grant_types`
- Waliduj `redirect_uris` przeciw allowlist (patrz sekcja Bezpieczeństwo)
- Waliduj `grant_types` — dozwolone tylko `authorization_code` i `refresh_token`

### 3. Authorization endpoint

#### `GET /authorize`

Wyświetla formularz logowania lub ekran consent.

**Query params:**
| Param | Wymagany | Opis |
|---|---|---|
| `client_id` | tak | ID zarejestrowanego klienta |
| `redirect_uri` | tak | Musi być w `redirect_uris` klienta |
| `response_type` | tak | Zawsze `code` |
| `state` | nie | CSRF token od klienta |
| `code_challenge` | tak | PKCE challenge (SHA256) |
| `code_challenge_method` | tak | Zawsze `S256` |
| `scope` | nie | Opcjonalny zakres |

**Logika:**
1. Sprawdź czy `client_id` istnieje w DB
2. Sprawdź czy `redirect_uri` jest w liście `redirect_uris` klienta
3. Jeśli użytkownik niezalogowany → pokaż formularz logowania
4. Jeśli zalogowany → pokaż ekran consent (zezwól / odmów)

#### `POST /authorize`

Obsługuje 3 akcje (pole `action` w formularzu):

**a) `action=login`** — logowanie:
- Zweryfikuj email + hasło
- Ustaw sesję użytkownika
- Redirect 303 z powrotem na `GET /authorize` (z tymi samymi parametrami) → wyświetli consent

**b) `action=consent`** — użytkownik zatwierdza:
- Wygeneruj jednorazowy authorization code (`secrets.token_urlsafe(48)`)
- Zapisz w DB: `code`, `client_id`, `user_id`, `redirect_uri`, `code_challenge`, `code_challenge_method`, `expires_at` (10 minut)
- Redirect 302 na `redirect_uri?code=<code>&state=<state>`

**c) `action=deny`** — użytkownik odmawia:
- Redirect 302 na `redirect_uri?error=access_denied&state=<state>`

**Formularz HTML** — musi przekazywać wszystkie parametry OAuth jako hidden fields:
```html
<input type="hidden" name="client_id" value="{{ client_id }}">
<input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
<input type="hidden" name="state" value="{{ state }}">
<input type="hidden" name="code_challenge" value="{{ code_challenge }}">
<input type="hidden" name="code_challenge_method" value="{{ code_challenge_method }}">
<input type="hidden" name="scope" value="{{ scope }}">
<input type="hidden" name="action" value="login|consent|deny">
```

### 4. Token endpoint

#### `POST /token`

**Content-Type:** `application/x-www-form-urlencoded` (standard OAuth!)

**a) Wymiana code na tokeny** (`grant_type=authorization_code`):

| Param | Wymagany | Opis |
|---|---|---|
| `grant_type` | tak | `authorization_code` |
| `client_id` | tak | ID klienta |
| `code` | tak | Authorization code z redirect |
| `code_verifier` | tak | PKCE verifier (plaintext) |
| `redirect_uri` | tak | Musi pasować do oryginału |

**Walidacja:**
1. Sprawdź `client_id` istnieje
2. Znajdź authorization code w DB (po `code` + `client_id`)
3. Sprawdź czy nie wygasł (`expires_at > now()`)
4. Sprawdź `redirect_uri` pasuje do zapisanego
5. **Zweryfikuj PKCE:** `SHA256(code_verifier)` == zapisany `code_challenge`
6. Usuń authorization code z DB (jednorazowy!)
7. Wygeneruj access token + refresh token

**PKCE S256 weryfikacja:**
```python
import base64, hashlib, hmac

def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, code_challenge)
```

**Response** (200):
```json
{
  "access_token": "losowy_token_urlsafe_48",
  "token_type": "bearer",
  "expires_in": 2592000,
  "refresh_token": "losowy_refresh_token"
}
```

**b) Odświeżanie tokenu** (`grant_type=refresh_token`):

| Param | Wymagany | Opis |
|---|---|---|
| `grant_type` | tak | `refresh_token` |
| `client_id` | tak | ID klienta |
| `refresh_token` | tak | Refresh token |

**Walidacja:**
1. Sprawdź `client_id` istnieje
2. Znajdź refresh token w DB (po hash + `client_id`, `is_revoked=false`)
3. Sprawdź czy nie wygasł
4. Wygeneruj NOWY access token + NOWY refresh token (rotacja!)
5. Oznacz stary refresh token jako `is_revoked=true`

**Response:** taka sama struktura jak wyżej.

**Błędy** (format OAuth):
```json
{"error": "invalid_grant", "error_description": "Opis błędu"}
{"error": "invalid_client", "error_description": "Opis błędu"}
{"error": "invalid_request", "error_description": "Opis błędu"}
{"error": "unsupported_grant_type", "error_description": "Opis błędu"}
```

---

## Modele bazodanowe (4 tabele)

### `oauth_clients`
| Kolumna | Typ | Opis |
|---|---|---|
| `id` | UUID PK | |
| `client_id` | VARCHAR(255) UNIQUE INDEX | Identyfikator klienta |
| `client_name` | VARCHAR(255) NULL | Nazwa wyświetlana |
| `redirect_uris` | JSON | Lista dozwolonych redirect URI |
| `grant_types` | JSON | Lista dozwolonych grant types |
| `client_secret` | VARCHAR(255) NULL | Nieużywane (public clients) |
| `created_at` | TIMESTAMPTZ | |

### `oauth_authorization_codes`
| Kolumna | Typ | Opis |
|---|---|---|
| `id` | UUID PK | |
| `code` | VARCHAR(255) UNIQUE INDEX | Jednorazowy kod |
| `client_id` | VARCHAR(255) | Ref do klienta |
| `user_id` | UUID FK → users.id INDEX | |
| `redirect_uri` | VARCHAR(2048) | Zweryfikowany URI |
| `scope` | VARCHAR(255) NULL | |
| `code_challenge` | VARCHAR(255) | PKCE challenge |
| `code_challenge_method` | VARCHAR(10) | Zawsze `S256` |
| `expires_at` | TIMESTAMPTZ | TTL 10 minut |
| `created_at` | TIMESTAMPTZ | |

### `oauth_access_tokens`
| Kolumna | Typ | Opis |
|---|---|---|
| `id` | UUID PK | |
| `token_hash` | VARCHAR(255) UNIQUE INDEX | SHA256 hash tokenu |
| `client_id` | VARCHAR(255) | |
| `user_id` | UUID FK → users.id INDEX | |
| `scope` | VARCHAR(255) NULL | |
| `expires_at` | TIMESTAMPTZ | TTL 30 dni |
| `created_at` | TIMESTAMPTZ | |

### `oauth_refresh_tokens`
| Kolumna | Typ | Opis |
|---|---|---|
| `id` | UUID PK | |
| `token_hash` | VARCHAR(255) UNIQUE INDEX | SHA256 hash |
| `access_token_id` | UUID FK → oauth_access_tokens.id | Powiązany access token |
| `client_id` | VARCHAR(255) | |
| `user_id` | UUID FK → users.id INDEX | |
| `expires_at` | TIMESTAMPTZ | TTL 30 dni |
| `is_revoked` | BOOLEAN DEFAULT false | Rotacja tokenów |
| `created_at` | TIMESTAMPTZ | |

---

## Bezpieczeństwo

### Allowlist redirect URIs

Przy rejestracji klienta waliduj `redirect_uris` — dozwolone:
```
https://claude.ai/api/mcp/auth_callback
https://claude.com/api/mcp/auth_callback
http://localhost:* (dowolny port, dowolna ścieżka — dev)
http://127.0.0.1:* (dowolny port — dev)
```

### Tokeny — NIGDY nie przechowuj raw tokenów

- W DB zapisuj **tylko SHA256 hash** (`hashlib.sha256(token.encode()).hexdigest()`)
- Raw token zwracasz klientowi tylko raz (w response)
- Przy weryfikacji: hash przychodzącego tokenu i szukaj w DB po hashu

### PKCE jest obowiązkowe

- `code_challenge_method` musi być `S256` (odrzuć `plain`)
- Bez PKCE nie wydawaj authorization code

### Authorization codes — jednorazowe

- Po wymianie na tokeny: **usuń z DB natychmiast**
- Krótki TTL (10 minut)

### Refresh token rotation

- Przy odświeżaniu: stary refresh token → `is_revoked=true`, nowy refresh token
- Zapobiega replay attacks

---

## Weryfikacja tokenu w MCP serverze

Gdy MCP tool otrzymuje request:

```python
# 1. Wyciągnij token z nagłówka
auth_header = request.headers.get("Authorization", "")
if not auth_header.startswith("Bearer "):
    raise ValueError("Brak tokenu")
raw_token = auth_header[7:]

# 2. Zweryfikuj w DB
token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
# SELECT user.* FROM users
#   JOIN oauth_access_tokens ON oauth_access_tokens.user_id = users.id
#   WHERE oauth_access_tokens.token_hash = :hash
#     AND oauth_access_tokens.expires_at > NOW()
#     AND users.is_active = true
```

---

## Montowanie MCP servera (FastAPI + FastMCP)

```python
from fastmcp import FastMCP, TransportSecuritySettings

mcp = FastMCP(
    "NazwaTwojejAplikacji",
    instructions="Opis co robi Twój MCP server...",
    streamable_http_path="/",
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
                       "twoja-domena.com"],
    ),
)

# Mount w FastAPI app
mcp_http_app = mcp.streamable_http_app()
app.mount("/mcp", mcp_http_app)

# Claude Desktop łączy się na APP_URL (bez /mcp/), więc mount też na root:
app.mount("/", mcp_http_app)
```

**Ważne:** Session manager musi być uruchomiony w lifespan:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)
```

---

## Flow krok po kroku

```
Claude Desktop                     Twoja aplikacja
     |                                    |
     |-- GET /.well-known/oauth-protected-resource -->|
     |<-- {resource, authorization_servers} ----------|
     |                                    |
     |-- GET /.well-known/oauth-authorization-server ->|
     |<-- {endpoints, grant_types, PKCE} -------------|
     |                                    |
     |-- POST /register ------------------>|
     |   {redirect_uris, grant_types}      |
     |<-- {client_id} --------------------|
     |                                    |
     |  (generuje code_verifier + challenge)
     |                                    |
     |-- Otwiera przeglądarkę:            |
     |   GET /authorize?client_id=...     |
     |   &code_challenge=...&redirect_uri=...|
     |                                    |
     |   Użytkownik loguje się na stronie  |
     |   POST /authorize (action=login)   |
     |   303 → GET /authorize (consent)   |
     |                                    |
     |   Użytkownik klika "Zezwól"        |
     |   POST /authorize (action=consent) |
     |   302 → redirect_uri?code=abc123   |
     |                                    |
     |<-- callback z code ----------------|
     |                                    |
     |-- POST /token -------------------->|
     |   grant_type=authorization_code    |
     |   code=abc123                      |
     |   code_verifier=...                |
     |<-- {access_token, refresh_token} --|
     |                                    |
     |== Teraz używa MCP tools ===========|
     |-- POST /mcp (Authorization: Bearer <token>) -->|
     |<-- wyniki narzędzi MCP ------------|
     |                                    |
     |  (po wygaśnięciu access tokenu)    |
     |-- POST /token -------------------->|
     |   grant_type=refresh_token         |
     |<-- {new access_token, new refresh_token} -|
```

---

## Konfiguracja (env vars)

```env
APP_URL=https://twoja-aplikacja.com    # Bazowy URL (używany w metadata)
SECRET_KEY=losowy-klucz-sesji          # Do podpisywania session cookies
OAUTH_ACCESS_TOKEN_TTL=2592000         # 30 dni w sekundach
OAUTH_REFRESH_TOKEN_TTL=2592000        # 30 dni
OAUTH_AUTH_CODE_TTL=600                # 10 minut
```

---

## Checklist implementacji

- [ ] 4 tabele w DB (migration): `oauth_clients`, `oauth_authorization_codes`, `oauth_access_tokens`, `oauth_refresh_tokens`
- [ ] `GET /.well-known/oauth-protected-resource` — resource metadata
- [ ] `GET /.well-known/oauth-authorization-server` — server metadata
- [ ] `POST /register` — dynamic client registration
- [ ] `GET /authorize` — formularz logowania + consent
- [ ] `POST /authorize` — obsługa login / consent / deny
- [ ] `POST /token` — wymiana code na tokeny + refresh
- [ ] Template HTML dla authorize (login + consent w jednym)
- [ ] PKCE S256 weryfikacja
- [ ] SHA256 hashing tokenów (nigdy raw w DB)
- [ ] Refresh token rotation (is_revoked)
- [ ] Allowlist redirect URIs
- [ ] Weryfikacja Bearer token w MCP server
- [ ] Mount MCP server + session manager lifecycle
- [ ] Session middleware (do logowania w /authorize)

---

## Zależności

```
fastmcp>=2.0          # MCP server
starlette              # SessionMiddleware (w FastAPI jest wbudowane)
```

FastMCP 2.x obsługuje `streamable_http_app()` i `TransportSecuritySettings`.
