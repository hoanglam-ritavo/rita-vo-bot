# Shared HTTP service authentication

Every HTTP deployment requires a nonblank private `CHAT_BOT_SERVICE_KEY`, even
when conversation and Type B are disabled. The key is excluded from settings
repr and logs; HTTP retains `.env`-before-process precedence. TYPE_B_SERVICE_KEY
is retired, ignored, logged by name only and never used as a fallback.

All `/api/v1` requests, `/health/ready` and `/health/answer` require exactly one
matching `X-Rita-Service-Key` header. A shared pure ASGI middleware returns
401/no-store for missing, wrong or duplicate headers before body processing,
source queries or turn reservation. It forwards receive/send unchanged so worker
disconnect/cancellation/draining remain intact. Backend still validates current
employee access, room ownership and HRM references; the key identifies a service.

`/health/live` remains a minimal unauthenticated process probe. Swagger/OpenAPI
remain local operational tools. Conversation, answer and Type B keep their
independent capability switches. Source coordinators no longer accept credentials
or HTTP bypass flags; CLI/direct Python functions do not need a network key.

For local probes supply the private key through the process environment without
printing it; this example does not load `.env` or contain a real key:

```powershell
$probeHeaders = @{ 'X-Rita-Service-Key' = $env:CHAT_BOT_SERVICE_KEY }
Invoke-RestMethod -Uri http://127.0.0.1:8000/health/live -TimeoutSec 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/health/ready -Headers $probeHeaders -TimeoutSec 20
Invoke-RestMethod -Uri http://127.0.0.1:8000/health/answer -Headers $probeHeaders -TimeoutSec 20
```

Python still binds `127.0.0.1`. A reverse proxy on the bot's machine/network
namespace can expose `/api/v1/` over HTTPS to a Backend on another domain,
public/private network or machine. Backend requires an origin and matching key;
its timeout is fixed at 60 seconds and top-K at 5. It validates TLS certificates
and hostnames and does not follow redirects. Bot deadline/busy/source/body
contracts remain unchanged.

The Backend repository's `docs/chat-bot-https.md` owns the Nginx template and
rollout. Forward the supplied header, never inject a valid credential for an
unauthenticated public caller. Do not publish health/docs/OpenAPI through that
proxy or disable TLS verification. CORS/same-domain restrictions do not govern
server-to-server requests. This change installs no proxy/certificate or remote
Python bind and performs no deployment.

Old stateless HTTP clients/probes must now send the shared key. Inventory them,
update a compatible Backend/bot pair while public routing remains closed, then
verify local authentication and designated synthetic requests before publishing
HTTPS. Roll back compatible applications as a pair, retaining messages,
tombstones and purge jobs. Do not expose the old unauthenticated boundary
remotely. Bot conversation must remain enabled while purges need servicing.

Tests use fictional credentials and mock sources. Backend separately exercises
ephemeral HTTPS with test-owned certificates. They do not establish live DNS,
proxy, PostgreSQL or semantic answer-quality acceptance. See
[verification](../README.md#current-verification-status).
