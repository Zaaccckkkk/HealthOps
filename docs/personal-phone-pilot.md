# Personal iPhone acceptance pilot

The personal pilot uses a separate Cloud Run service, SQL database, runtime identity and secret
inside the existing team project. **Project administrators can still access these resources.**
This is operational separation from synthetic evaluation, not isolation from project Owners.
The old personal-history VM is not copied or migrated by this workflow.

## Authentication and storage

`healthops-personal-api` allows HTTPS invocation without Google IAM credentials. Every health-data,
tool, identity and assistant route still requires the existing random HealthOps bearer token.
The token is operator-issued, stored only as a SHA-256 hash in PostgreSQL, expires after seven days,
and can be revoked. There is no public account creation or password login. This is a single-owner
pilot, not a complete consumer authentication system. The synthetic service retains Cloud Run IAM.
See [Cloud Run end-user authentication](https://docs.cloud.google.com/run/docs/authenticating/end-users)
for the distinction between end users and Google Cloud service identities.

The iOS Keychain now binds a token to its server URL and uses device-only, unlocked accessibility.
Old unscoped tokens are not reused; save the supplied account token again after updating the app.
Changing the server or token invalidates verified identity and pending UI state. No Google service
account JSON, SQL password, or project administrator credential belongs on the phone.

## Preventing environment mixups

`GET /v1/me` returns `data_mode`: `development`, `synthetic`, or `personal`. Deployment owns this
setting; it is not guessed from an account's name. A physical iPhone refuses HealthKit upload
unless the verified server declares `personal` and uses HTTPS. A Simulator refuses personal servers.
An older server without this field remains readable but cannot receive HealthKit uploads from
the updated client until its environment is explicitly configured.

Uploads carry `X-HealthOps-Data-Kind: personal` on an iPhone and `synthetic` on a Simulator.
The API rejects mismatches and missing declarations in non-development environments before writing
observations. This prevents accidental mixing by the supported client; the header is not proof
that an adversarial payload is synthetic. Server authentication and user-scoped queries remain required.
The cloud synthetic importer explicitly declares `synthetic`.

## First device acceptance

1. Install the signed app with Xcode and keep the phone unlocked for pairing. Signing identity and
   provisioning files remain local; no Team ID is committed.
2. In Setup, enter the personal HTTPS service URL, save its private account token, and connect.
   Confirm the account and **personal** environment before granting consent.
3. Request HealthKit read access. This does not itself authorize upload. iOS does not disclose
   whether particular read permissions were denied; empty results can mean no accessible data.
4. Start with the default selection: **steps only, from yesterday's local midnight**. Review and
   grant upload consent, then tap Sync now. No automatic upload runs on launch or connection.
5. Check the Overview chart against accessible Apple Health records. Repeat Sync now: unchanged
   records should produce no new observations (or duplicates after an explicit replay).
6. Ask an explicit dated question, then a vague question and its clarification reply. Check the
   requested date, units, data channel, missing-record handling and the verified/unverified label.
   Do not assume a verified card proves the router understood the original question.
7. Widen dates or enable sleep/resting heart rate only after reviewing new consent. Sleep totals
   use the existing local-start-day aggregation, not Apple's source-priority algorithm.

Real values, screenshots, questions and raw responses must remain private. Do not add them to the
synthetic evaluation set, public documentation, or source control. Device acceptance requires an
actual user interaction; a successful build or an empty-account cloud smoke is not that evidence.

## Deployment

Create an empty `healthops_personal` database with separate migration/runtime PostgreSQL roles.
Revoke PUBLIC database access and grant the runtime only CONNECT, schema USAGE, table CRUD and
required sequence access. Keep SQL URLs and account credentials in ignored, mode-0600 local files.
The runtime service account reads only its own database secret. It does not need GCS model access.
Project/database administrators remain privileged, as acknowledged above.

Use the reconciliation image build and a private config derived from the cloud example, with:

- service: `healthops-personal-api`
- database_secret: `healthops-personal-database-url`, pinned version
- service_account: `healthops-personal-run@YOUR_PROJECT.iam.gserviceaccount.com`
- reconcile_model_dir: `/app/models/reconcile`
- model_url: the current private Ollama endpoint

```bash
python3 src/deployment/cloud-run/deploy_personal.py --config .local/cloud/personal-run.json
# Only after authorization, with the separate database and secret ready:
python3 src/deployment/cloud-run/deploy_personal.py --config .local/cloud/personal-run.json --apply
```

The script refuses the synthetic service name, shared runtime identity or synthetic secret.
It sets personal data mode and disables evaluation date overrides. CPU/memory/instance limits
remain the same as the synthetic deployment. The shared GPU still has a two-hour automatic STOP;
SQL and Cloud Run do not start it automatically. Model prompts/evidence pass through that shared
project's model server, so this is not confidentiality isolation from its administrators.

Reconciliation is synthetic-trained and not clinically validated. Initial acceptance should focus
on recorded observations; an estimate or device attribution does not establish physiological truth.

Deployment observations and remaining device checks are recorded in the
[cloud preparation evidence](evidence/personal-phone-cloud-2026-11-08.md).

## Optional Google Health connection

The personal deployment can reuse an existing Google OAuth **Web application** client. Add
`https://YOUR_PERSONAL_SERVICE.run.app/v1/connections/google-health/callback` to that client's
allowed redirect URIs and keep the existing redirect for any still-supported deployment.
Google Health API access, consent-screen scopes and test-user eligibility must be configured in
the OAuth client's project. Enabling the connector is not evidence that the owner has authorized it.

The private deployment JSON accepts this optional block (the client ID here is illustrative):

```json
{
  "url": "https://healthops-personal-api-123.us-east4.run.app",
  "google_oauth": {
    "client_id": "123-fixture.apps.googleusercontent.com",
    "client_secret_version": "1",
    "connection_key_version": "1"
  }
}
```

Store the client secret in `healthops-personal-google-client-secret` and a fresh Fernet key in
`healthops-personal-connection-key` in Secret Manager. Grant the personal runtime identity access
to those two secrets only, and pin numeric versions in the private config. Never store secret
values in that JSON or Git. Keep the encryption key stable: replacing it without re-encryption
would make already stored refresh tokens unreadable. The deployment renderer retains the database
secret and adds these references; raw secrets and unpinned versions are rejected.

After deployment, the owner selects Connect Google Health on the phone and authorizes the linked
Google/Fitbit account. The browser returns to the public HTTPS callback; a one-time server-side
state binds it to the authenticated HealthOps user. The server exchanges the code and encrypts the
refresh token. The phone receives only a completion status. Sync remains an explicit action and
stores only supported Fitbit-origin records under that same personal account; Apple Health copies
returned by Google are skipped to avoid importing the same channel twice.

### Heart-rate source coverage

All channels searches records stored in HealthOps for the requested metric; it does not fetch
every metric from upstream apps during a question. The Google connector imports steps, daily
resting heart rate, RMSSD HRV and sleep, but currently omits high-frequency heart-rate samples.
`heart_rate` and `resting_heart_rate` are different metrics and are never substituted for each other.

For `heart_rate` summaries/comparisons with All channels or Google Health selected, the assistant
adds a source limitation to the answer and evidence cards, including when claim verification fails.
This is a connector capability notice, not a claim that the user's Fitbit has no readings.
Explicit Apple Health queries do not receive that Google notice. Existing stored Google heart-rate
records, if any, remain queryable; the notice does not filter them out. Ask for `resting_heart_rate`
to inspect the daily resting-heart-rate records imported by the Google connector.
