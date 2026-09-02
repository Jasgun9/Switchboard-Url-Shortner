# Switchboard

A link shortener with click analytics, QR codes, expiring and password-protected
links, and a REST API. Django with server-rendered templates for the UI, Django
REST Framework for the API, PostgreSQL and Redis on a plain Linux VPS. No Node
build step and no client-side framework.

The name is the shape of the thing: a switchboard takes an incoming connection,
routes it to a destination, and logs the call.

Two separately deployed applications share one database and one Redis:

| Application | Host | Responsibility |
|---|---|---|
| Web app | `switchboard.jasgun.me` | UI, authentication, dashboard, link management, API, API keys, analytics, QR codes |
| Redirect service | `sb.jasgun.me` | Resolving short codes, redirecting, expiry, password gate, caching, rate limiting, click dispatch |

## Contents

- [Architecture](#architecture)
- [Database design](#database-design)
- [Redis](#redis)
- [Celery](#celery)
- [Authentication and authorization](#authentication-and-authorization)
- [API](#api)
- [API keys](#api-keys)
- [Analytics](#analytics)
- [Security](#security)
- [Local setup](#local-setup)
- [Environment variables](#environment-variables)
- [Testing](#testing)
- [Production setup](#production-setup)
- [Tradeoffs](#tradeoffs)
- [Limitations](#limitations)

## Architecture

```
Cloudflare
  ├── switchboard.jasgun.me ──► nginx ──► gunicorn (config.wsgi_web)      ─┐
  └── sb.jasgun.me         ──► nginx ──► gunicorn (config.wsgi_redirect) ─┤
                                                                           ├─► PostgreSQL
                                          celery worker  ──────────────────┤
                                          celery beat    ──────────────────┘
                                                                           └─► Redis
```

Both processes run the same Django project. They differ only in which URLconf
they mount:

- `config/urls_web.py` — pages, API, admin, health
- `config/urls_redirect.py` — `/<code>`, health, robots

That is the whole separation mechanism. Business logic lives in the `shortener`
app and is imported by both, so nothing is duplicated, while the redirect host
genuinely cannot serve the dashboard or the API even if someone points DNS at
the wrong process.

```
backend/
  config/         settings, celery, the two URLconfs, the two WSGI entry points
  core/           cross-cutting helpers: redis client, rate limiting, client IP, health
  accounts/       the user model
  shortener/      models, validation, code generation, cache, QR, GeoIP, UA parsing, tasks
  api/            DRF serializers, views, API-key authentication, error envelope
  web/            the HTML UI: forms and views over the same models
  redirector/     the redirect views, password gate and their templates
  templates/      web/ and redirector/ templates
  static/         app.css plus three small JS files
  tests/
deploy/           nginx sites and systemd units
```

### Front end

No build step and no framework: Django templates, one stylesheet driven by CSS
custom properties, and three plain JavaScript files.

- `static/app.css` — the design system, driven entirely by custom properties:
  a soft blue-gray canvas (`#F3F6FD`), white cards, and one corporate blue
  accent (`#4F6EF7`) that also carries the charts. Plus Jakarta Sans for the
  interface, JetBrains Mono for data (codes, counts, hosts, dates). Radii,
  elevation and spacing come from fixed scales — cards 18–22px, buttons and
  inputs 12px, badges fully rounded; shadows never exceed
  `0 14px 40px rgba(16,24,40,.09)`. Light theme only.
- `static/js/ui.js` — shared behaviour: entrance animation, mobile nav,
  dropdown, confirm dialogs, copy buttons, progressive disclosure, password
  reveal, submit loading states.
- `static/js/shorten.js` — the home page. The form posts normally without
  JavaScript; with it, the same REST API the docs describe is called and the
  form is swapped for the result in place.
- `static/js/docs.js` — the API reference sidebar (scroll spy, smooth jump).

Two animation libraries load from CDN, both deferred: **GSAP** for sequenced
work (page entrance, the shorten transition, the advanced-options expand) and
**Motion** for one-off element states (dropdown, dialog, copy confirmation,
mobile nav) plus `inView` scroll triggers. Durations sit between 200 and 600ms.

Only blocks already on screen animate on load; anything below the fold is
revealed by `Motion.inView` as it is scrolled to, so a visitor who scrolls
straight away never meets an empty section. Every animated path checks
`prefers-reduced-motion` and falls back to an instant state change, and the
entrance targets are released by a CSS class with a 1.2s timeout failsafe, so
the page still shows itself if the script never arrives.

Icons are one inline SVG sprite in `templates/web/_icons.html` — no icon font,
no dependency.

### Crawlers

Only two pages are meant to be indexed: the home page and the API reference.
The base template sets `noindex, nofollow` by default and those two opt back in,
so a page added later is private until someone deliberately opens it up rather
than the other way round.

`robots.txt` disallows `/admin/`, `/api/`, and the signed-in pages, and points
at `/sitemap.xml`. The sitemap, the canonical tag and `og:image` are all built
from `WEB_DOMAIN` rather than the request's `Host` header, so a crawler that
reaches the origin by IP still gets canonical URLs back and a poisoned Host
can't rewrite them.

Each page carries its own title, description and Open Graph tags. The social
card at `static/og.png` is rendered from `scratchpad/og.html` in headless
Chrome, which is how it gets the real brand fonts; re-render it if the wording
changes.

The HTML pages and the API are two front doors onto the same models. The pages
use the ORM directly through Django forms rather than calling the API over HTTP
— going out to your own API from your own view process would add a network hop
and a second authentication path for nothing. The shared part is the domain
logic in `shortener/`: `create_short_url()`, the validators and
`analytics.summary()` are called by both.

## Database

SQLite locally, **PostgreSQL** in production. `DATABASE_URL` is the whole
switch — no application code branches on the backend, and the only raw SQL in
the project is the `SELECT 1` in the readiness probe.

```bash
DATABASE_URL=postgres://switchboard:password@127.0.0.1:5432/switchboard
```

**MySQL is not supported, and the reason is specific.** Short codes are kept
unique by a partial constraint:

```sql
UNIQUE (code) WHERE code_released_at IS NULL
```

MySQL cannot create partial indexes (`supports_partial_indexes = False` in
Django's MySQL backend). Django only emits a *warning* for this and then skips
the constraint, so `migrate` succeeds and you end up with **no uniqueness on
short codes at all** — two live links can claim the same alias and the
`IntegrityError` that decides contested claims never fires. That failure is
silent, which is exactly why `shortener/checks.py` turns it into a startup
error (`shortener.E002`) rather than letting it through.

If MySQL is a hard requirement, the portable fix is to drop the condition and
add a nullable `active_code` column holding the code while the link is live and
`NULL` once released, with a plain unique index on it. Every backend treats
NULLs as distinct in a unique index, so that gives the same guarantee. It is a
schema change plus a migration, not a settings change.

A second check (`shortener.E001`, deploy-only) fails `manage.py check --deploy`
if SQLite is still configured, since one writer on one box cannot back two
Gunicorn services and a Celery worker.

Connections are reused for `DB_CONN_MAX_AGE` seconds with `CONN_HEALTH_CHECKS`
on, so a connection that died while the database restarted is replaced instead
of raising.

## Database design

Four tables carry the product.

**`accounts_user`** — email is the login field and is unique. Passwords go
through Django's hasher; nothing custom.

**`shortener_shorturl`**

| Column | Notes |
|---|---|
| `code` | `UNIQUE`. Both random codes and custom aliases live here, so the database is what resolves a race between two people claiming the same alias. |
| `destination` | `TEXT`, validated but never fetched. |
| `owner` | Nullable FK — anonymous visitors can shorten a link without an account. |
| `password_hash`, `password_updated_at` | Hashed link password; the timestamp is what invalidates unlock cookies when the password changes. |
| `expires_at`, `is_active`, `deleted_at` | The three ways a link stops resolving. |
| `click_count`, `last_clicked_at` | Denormalised counters, updated by the worker with an atomic `UPDATE`. Keeps the dashboard from running `COUNT(*)` per row. |

Indexes: the unique index on `code` (the redirect lookup), `(owner, -created_at)`
(the dashboard listing), and a partial index on `expires_at WHERE expires_at IS
NOT NULL` (only a minority of links expire).

**`shortener_clickevent`** — one row per click. Indexed on
`(short_url, -created_at)` for the analytics queries and on `created_at` for the
retention purge. There is no JSON blob of analytics anywhere.

**`shortener_apikey`** — `prefix` is unique and indexed; it is the lookup key.
`secret_hash` holds a SHA-256 digest of the secret half.

Everything is timezone-aware (`USE_TZ = True`, UTC in the database).

### Deletion behaviour

`DELETE /api/v1/urls/{id}/` is a **soft delete**: `deleted_at` is set, the link
stops resolving immediately, the Redis entry is dropped, and the click history
survives so historic analytics stay meaningful.

**The code is released for reuse.** `code_released_at` is stamped at the same
time, and the uniqueness rule is a *partial* constraint:

```python
UniqueConstraint(fields=["code"], condition=Q(code_released_at__isnull=True))
```

Only one row may own a code at a time, but released rows drop out of the index,
so their alias becomes claimable again while their history stays put. Expired
links work the same way, released lazily at the moment someone claims the alias
(expiry is time-dependent, so it cannot live in a database constraint).
Disabled links keep their code — switching a link off is meant to be reversible.

A nightly Celery job hard-deletes links soft-deleted for more than 30 days,
taking their click rows with them (`ON DELETE CASCADE`). That grace period is
retention, not reservation.

## Redis

Redis is used for three distinct things, all of which degrade rather than fail
when it is unavailable.

**Resolve cache.** `resolve:<code>` holds a small JSON payload — id, destination,
`has_password`, `expires_at`, `password_version`. Unknown codes are cached as a
sentinel for 60 seconds so scanners walking random codes never reach PostgreSQL.

Invalidation is handled in three overlapping ways, because getting this wrong is
how a shortener keeps serving a link that was deleted an hour ago:

1. `ShortURL.save()` and `.delete()` drop the key. Every write path — API, admin,
   Celery — goes through them.
2. The cache TTL is capped at the link's remaining lifetime, so an entry can
   never outlive the link.
3. The redirect view re-checks `expires_at` from the cached payload before
   redirecting.

**Rate limiting.** A fixed-window counter (`INCR` + `EXPIRE` in one round trip) in
`core/ratelimit.py`. Limits live in `settings.RATE_LIMITS` and are configurable
through the environment; they are not scattered through view code.

**Celery broker.** Redis carries the click-recording queue.

`CACHES` is configured with `IGNORE_EXCEPTIONS`, so a Redis outage turns cache
reads into misses and the site keeps working straight off PostgreSQL. Rate
limiting fails **open** with a warning — locking every user out of login for the
duration of a Redis outage is worse than the abuse it would prevent. Redis is
never the source of truth for anything.

## Celery

The only work dispatched per request is recording a click. The redirect handler
collects the raw request data and calls `enqueue_click()`, which swallows broker
connection errors and logs them. If the broker is down, redirects keep working
and the analytics for that window are lost — the right trade for a redirector.

GeoIP lookup and user-agent parsing happen in the worker, never in the request.

Beat runs two nightly jobs, both of which are genuinely periodic:

- `purge_old_clicks` — deletes click rows older than `CLICK_RETENTION_DAYS`.
- `purge_deleted_urls` — hard-deletes links soft-deleted more than 30 days ago.

## Authentication and authorization

Session authentication with Django's own `contrib.auth`. No JWT: the UI is
server-rendered on the same origin, so an `HttpOnly`, `Secure`, `SameSite=Lax`
session cookie carries everything and there is no token for JavaScript to leak.
Machine clients use API keys instead. Every form posts a CSRF token.

Authorization is enforced by scoping every queryset to `request.user`:

```python
def get_queryset(self):
    return ShortURL.objects.filter(owner=self.request.user, deleted_at__isnull=True)
```

Requesting someone else's id returns 404, not 403 — there is no oracle telling
you which ids exist. The same applies to analytics and API keys.

## API

Everything lives under `/api/v1/`.

```
POST   /api/v1/urls/                  create (works unauthenticated, lower limit)
GET    /api/v1/urls/                  list, with ?search= ?status= ?ordering= ?page= ?page_size=
GET    /api/v1/urls/{id}/             retrieve
PATCH  /api/v1/urls/{id}/             update
DELETE /api/v1/urls/{id}/             soft delete
GET    /api/v1/urls/{id}/analytics/   ?days=1..365
GET    /api/v1/api-keys/              list
POST   /api/v1/api-keys/              create (secret returned once)
DELETE /api/v1/api-keys/{id}/         revoke
GET    /api/v1/qr/{code}.png          QR image (public)
POST   /api/v1/auth/register|login|logout, GET /api/v1/auth/me
```

Errors always use one envelope:

```json
{ "error": { "code": "ALIAS_ALREADY_EXISTS", "message": "The requested alias is already in use." } }
```

Validation failures add `error.details` with the per-field messages. Codes in
use: `VALIDATION_ERROR`, `ALIAS_ALREADY_EXISTS`, `AUTHENTICATION_REQUIRED`,
`AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`,
`RATE_LIMITED`, `INVALID_CREDENTIALS`, `INVALID_PARAMETER`,
`CODE_GENERATION_FAILED`, `INTERNAL_ERROR`.

## API keys

Format: `usk_<8 hex prefix>_<43 char urlsafe secret>`, sent as
`Authorization: Bearer <key>`.

- The secret is 32 bytes from `secrets.token_urlsafe` and is returned exactly
  once, at creation.
- Only a SHA-256 digest is stored. SHA-256 rather than a password hasher is
  correct here: the secret is 256 bits of CSPRNG output, so there is no
  dictionary to run and no reason to pay a KDF on every request. Comparison is
  constant-time.
- The prefix is the indexed lookup column, which keeps authentication to a single
  query.
- `last_used_at` is written at most once a minute per key, so a busy key does not
  cause a row update per request.
- Revocation is immediate; optional expiry is supported.
- API keys **cannot manage API keys** — that viewset accepts session
  authentication only, so a leaked key cannot mint more keys.

## Analytics

The redirect path does no analytics work. It hands `(url_id, ip, user_agent,
referrer_host, timestamp)` to Celery and returns a 302 with
`Cache-Control: private, no-store` so neither Cloudflare nor the browser can
short-circuit a later expiry, deletion, or click.

The worker then:

- looks the IP up in a **local MaxMind GeoLite2 database** (`geoip2`) — no
  external API call per click. `shortener/geo.py` is the only module that knows
  where the data comes from, so swapping providers is a change to `lookup()`.
- parses the user agent with `user-agents` into device / browser / OS,
- stores a **salted SHA-256 digest of the IP**, truncated to 32 hex chars, never
  the address itself. That still supports unique-visitor counts.
- writes the `ClickEvent` and bumps the denormalised counters.

Analytics queries are plain grouped aggregates over the indexed click table
(`shortener/analytics.py`). There is no rollup table: at this scale a `GROUP BY`
over a bounded date range on an indexed column is fast, and a rollup would add a
second source of truth to keep consistent. If click volume ever justifies it, a
daily rollup written by Beat is the obvious next step.

Raw click rows are deleted after `CLICK_RETENTION_DAYS` (180 by default).

## Security

- **Destination URLs**: only `http` and `https`. `javascript:`, `data:`,
  `file:`, protocol-relative URLs, embedded credentials and control characters
  are rejected. Private, loopback and link-local hosts are rejected in
  production, as is a destination pointing back at the short domain.
- **No SSRF surface**: destinations are parsed, never requested. The application
  makes no outbound HTTP calls at all.
- **Aliases**: character allowlist, length bounds, case-folded, and a reserved
  list (`admin`, `api`, `login`, `dashboard`, `health`, `docs`, `static`,
  `robots.txt`, …).
- **Concurrency**: alias and code uniqueness are enforced by a database
  constraint and `IntegrityError` is handled — a check-then-insert would race.
  Random codes retry inside their own savepoint.
- **Link passwords**: hashed with Django's hasher, never stored or cached in the
  clear, verified behind a per-IP-per-link rate limit. A successful unlock issues
  a signed cookie scoped to that link's path and bound to `password_updated_at`,
  so changing the password revokes outstanding unlocks.
- **CSRF**: enabled site-wide; login, registration and logout are explicitly
  `csrf_protect`ed even though DRF exempts API views by default.
- **XSS / SQL injection**: React escapes by default and no `dangerouslySetInnerHTML`
  is used; all database access goes through the ORM with parameterised queries.
- **Rate limits** cover anonymous creation, authenticated creation, login,
  registration, link-password attempts, API requests and redirect traffic.
- **Request size** is capped at 512 KB in Django and in nginx.
- **Logging** records failure events (bad API key prefix, failed login, failed
  link password) but never passwords, key secrets or session identifiers.
- Stack traces are never returned in production; unhandled API exceptions are
  logged server-side and answered with `INTERNAL_ERROR`.

## Local setup

Requires Python 3.10+ and optionally Redis. PostgreSQL is not needed locally —
SQLite is the default and the application contains no database-specific logic.

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # then set DJANGO_DEBUG=1 and a DJANGO_SECRET_KEY
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
```

### Running both services

The two services are the same project with different URLconfs, so each needs its
own terminal. On Windows the venv binaries are under `.venv\Scripts\` and
environment variables are set with `$env:NAME = "value"`.

Set `DJANGO_ROOT_URLCONF` explicitly in **both** server terminals. It is an
environment variable, so it persists for the life of the shell: setting it once
for the redirect service and then starting the web service in the same terminal
silently gives you two redirect services.

| Terminal | Command | Serves |
|---|---|---|
| 1 | `DJANGO_ROOT_URLCONF=config.urls_web python manage.py runserver 8000` | UI + API on `http://localhost:8000` |
| 2 | `DJANGO_ROOT_URLCONF=config.urls_redirect python manage.py runserver 8001` | redirect service |
| 3 | `celery -A config worker -l INFO` | click processing (needs Redis) |
| 4 | `celery -A config beat -l INFO` | nightly jobs (needs Redis) |

Open `http://localhost:8000`. `SHORT_DOMAIN` defaults to
`http://localhost:8001`, so generated links point at terminal 2. On Windows the
worker needs `--pool=solo`.

If `http://localhost:8000/` redirects you somewhere else, that process is running
the redirect URLconf — `/` on the redirect host sends visitors to `WEB_DOMAIN` by
design.

### From another device on your network

To open the app on a phone or a second machine, three things have to line up.

```bash
python manage.py lanurl
```

prints your current LAN address, the two `.env` values to match it, and the
commands to run. Then:

1. **Bind to every interface**, not just loopback — `runserver 0.0.0.0:8000` and
   `runserver 0.0.0.0:8001`.
2. **Point `WEB_DOMAIN` and `SHORT_DOMAIN` at the LAN address.** These are baked
   into every generated short link and QR code, so leaving them on `localhost`
   sends the other device to *itself*. This is the step that is easy to miss:
   the site loads fine and every link it hands out is dead.
3. **Let the ports through the firewall.** On Windows, in an *administrator*
   PowerShell, scoped to your own subnet so it is not exposed more widely:

   ```powershell
   New-NetFirewallRule -DisplayName "Switchboard dev servers (LAN only)" `
     -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000,8001 `
     -Program "D:\url\backend\.venv\Scripts\python.exe" `
     -RemoteAddress LocalSubnet -Profile Private,Public
   ```

   To remove it later: `Remove-NetFirewallRule -DisplayName "Switchboard dev servers (LAN only)"`.

`ALLOWED_HOSTS` needs no change — with `DJANGO_DEBUG=1` it already accepts any
host. Your address will change when the router reassigns it, so re-run `lanurl`
if links suddenly point at the wrong machine.

This exposes an unhardened development server, with `DEBUG=1` and its
tracebacks, to everyone on that network. Fine on a home network; do not do it on
a public or shared one.

Without Redis the site still runs: the cache degrades to misses and rate limiting
fails open. Without a worker, redirects work and clicks are dropped with a
warning.

## Environment variables

See `backend/.env.example`. The ones that matter:

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Required in production. |
| `DJANGO_DEBUG` | `0` in production. Also switches on private-host destination blocking and secure cookies. |
| `DATABASE_URL` | `sqlite:///…` locally, `postgres://…` in production. |
| `REDIS_URL`, `CELERY_BROKER_URL` | Cache/limiter and broker. Use different Redis databases. |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Comma-separated. The SPA is served from the same origin as the API, so there is no CORS configuration to get wrong. |
| `SHORT_DOMAIN`, `WEB_DOMAIN` | Public origins of the two services. |
| `CLIENT_IP_HEADER` | `HTTP_CF_CONNECTING_IP` behind Cloudflare. Only this header is trusted for the client IP. |
| `GEOIP_PATH` | Path to `GeoLite2-City.mmdb`. Empty disables geographic analytics. |
| `IP_HASH_SALT` | Salt for click IP digests. Changing it makes historic unique counts discontinuous. |
| `CLICK_RETENTION_DAYS` | Raw click retention. |
| `RATE_LIMIT_*` | `requests,seconds` per scope. |

Secrets belong in `backend/.env`, which is gitignored, and are read by systemd
through `EnvironmentFile=`.

## Testing

```bash
cd backend
.venv/bin/python manage.py test tests
```

124 tests, no Redis or broker required — `tests/base.py` swaps in `fakeredis` and
a local-memory cache so cache and rate-limit behaviour is still exercised.

Covered: short code generation and collision retry, alias validation and
conflicts, malicious destinations, expiry, soft deletion, redirects, cache
population and every invalidation path, stale-cache expiry, negative caching,
password gate and unlock-cookie invalidation, rate limiting (including
fail-open on a Redis outage), authentication and authorization through both the
HTML views and the API (IDOR attempts on links, analytics and keys), open
redirect on `?next=`, POST-only destructive actions, API key lifecycle,
pagination, search and filtering, N+1 regressions, click recording, IP hashing,
retention jobs, and health checks.

## Production setup

PostgreSQL, Redis, nginx and Python run directly on the VPS. No containers.

```bash
sudo apt install python3-venv postgresql redis-server nginx
sudo -u postgres createuser switchboard --pwprompt
sudo -u postgres createdb switchboard -O switchboard

sudo adduser --system --group --home /srv/switchboard switchboard
sudo -u switchboard git clone <repo> /srv/switchboard
cd /srv/switchboard/backend
sudo -u switchboard python3 -m venv .venv
sudo -u switchboard .venv/bin/pip install -r requirements.txt
sudo -u switchboard cp .env.example .env   # fill it in, chmod 600
sudo -u switchboard .venv/bin/python manage.py migrate
sudo -u switchboard .venv/bin/python manage.py collectstatic --noinput

sudo cp /srv/switchboard/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now switchboard-web switchboard-redirect \
                            switchboard-worker switchboard-beat

sudo cp /srv/switchboard/deploy/nginx/*.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/switchboard.jasgun.me.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/sb.jasgun.me.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

GeoIP: download `GeoLite2-City.mmdb` (a free MaxMind account is required), put it
somewhere readable such as `/var/lib/geoip/`, point `GEOIP_PATH` at it, and
refresh it periodically with `geoipupdate`. The application picks up a replaced
file on the next worker restart.

### Cloudflare

- Both hostnames proxied (orange cloud), SSL mode **Full (strict)** with an
  origin certificate installed on the VPS.
- **Do not cache `sb.jasgun.me`.** Redirect responses already send
  `Cache-Control: private, no-store`, but add a cache rule that bypasses the
  cache for that hostname anyway — a cached 302 would keep an expired or deleted
  link alive and would hide clicks from analytics.
- Leave caching on for `switchboard.jasgun.me/static/*` and nothing else on that
  host — the pages are per-user.
- `CLIENT_IP_HEADER=HTTP_CF_CONNECTING_IP`, and restrict the origin firewall to
  Cloudflare's IP ranges so the header cannot be forged by connecting directly.

### Deploying an update

```bash
cd /srv/switchboard && sudo -u switchboard git pull
cd backend && sudo -u switchboard .venv/bin/pip install -r requirements.txt
sudo -u switchboard .venv/bin/python manage.py migrate
sudo -u switchboard .venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload switchboard-web switchboard-redirect
sudo systemctl restart switchboard-worker switchboard-beat
```

`/health/live` says the process is up; `/health/ready` checks PostgreSQL and
reports Redis. Redis being down does not make the service unready, because it
still serves correctly from the database.

## Tradeoffs

**One Django project, two URLconfs.** Two full projects would duplicate models,
settings and migrations for no benefit. One project with two entry points gives
independent processes, independent scaling and independent nginx sites while the
domain logic exists once.

**Two animation libraries.** GSAP alone would cover everything here, and Motion
is largely redundant next to it — about 25 KB gzipped of overlap. They are split
by job (sequences vs. single element states) and both load deferred, so the cost
is real but small. Dropping Motion and moving its four call sites to GSAP would
be a contained change.

**Server-rendered pages, with the REST API alongside rather than underneath.**
The UI is nine Django views over the ORM: no build step, no bundle, no token
storage, no client-side router, and one process to run. The cost is that
interactions are full page loads — the dashboard filters submit a form instead
of updating in place. For a tool whose pages are tables and forms that is a good
trade; an app with genuinely interactive state would not make the same one.

**Fixed-window rate limiting.** A sliding window or token bucket is more precise,
but a fixed window is one `INCR` and is trivial to reason about. It allows a
burst across a window boundary; for these limits that is acceptable.

**Rate limiting fails open.** If Redis is unreachable, requests are allowed and a
warning is logged. Failing closed would turn a cache outage into a total outage.

**No analytics rollup table.** Grouped queries over an indexed, date-bounded
click table are fast enough here, and a rollup would be a second thing to keep
correct. Documented as the next step rather than built speculatively.

**One click event per Celery task.** Batching would be more efficient at high
volume; at this volume it is unnecessary complexity, and per-task granularity
means one bad row cannot poison a batch.

**Released codes, and the takeover risk that comes with them.** Deleting or
expiring a link frees its alias immediately, so `/promo` can be used again next
quarter. The cost is real: a code printed on a flyer or sitting in an old chat
message can be claimed by someone else once the original expires, and their
destination is then what those scans reach. The alternative — reserving codes
forever — trades that risk for aliases that can never be reused. This build
chose reuse deliberately. If the exposure matters more than the convenience,
dropping the `release_reclaimable_code()` call in `create_short_url()` and
having `soft_delete()` stop setting `code_released_at` restores the old
behaviour without touching the schema.

**Anonymous links have no owner.** They can be created but never edited or
deleted through the API, because there is nobody to authorise. That is the
honest behaviour; the UI says so.

## Limitations

- Analytics are best-effort. If the broker is unavailable, clicks in that window
  are lost by design — the redirect matters more than the metric.
- Region and city accuracy depends on the GeoLite2 database and is often blank
  for mobile networks and VPNs. Countries are reliable; cities are not.
- Bot traffic is labelled, not filtered, so click counts include crawlers that
  follow links.
- Rate limits are per process-wide Redis counters keyed by IP or user id; a
  distributed abuser with many addresses is Cloudflare's problem, not the
  application's.
- There is no email delivery, so no password reset flow and no email
  verification.
- The QR endpoint is public. It only encodes the short URL, which is already
  public, but it does confirm that a code exists.
- No team or organisation accounts — every link belongs to exactly one user.
- Expiry and key-expiry times are entered and displayed in UTC. Detecting the
  visitor's timezone would mean a cookie handshake or a JavaScript conversion
  layer for two fields, so the fields are labelled instead.
