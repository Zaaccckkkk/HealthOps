# HealthOps Uploader (iOS adapter)

A SwiftUI app that reads HealthKit samples on the phone, asks for explicit upload consent, and
uploads canonical observations to the local HealthOps API with replay-safe incremental sync.
It replaces the display-string prototype outside this repository; nothing was copied from it.

Status: **verified in the iOS Simulator with synthetic samples only.** No physical iPhone or
Apple Watch data has been uploaded, the server still listens on loopback only, and no
Google Health / Fitbit path exists. Do not describe this as a live wearable integration.

## Layout

| Path | Role |
|---|---|
| `Package.swift`, `Sources/HealthOpsCore/` | HealthKit-independent core: contract encoding, sample mapping, sync engine, persistence, API client. Builds and tests on macOS. |
| `Sources/healthops-fixture/` | Prints the exact batch body for fixed synthetic samples; `tests/test_ios_contract.py` validates it with the Python schema. |
| `Tests/HealthOpsCoreTests/` | Mapping and sync-engine tests with fake HealthKit and fake server. |
| `App/` | SwiftUI app, `HKAnchoredObjectQuery` reader, Keychain token store, debug-only Simulator seeder. |
| `project.yml`, `HealthOpsUploader.xcodeproj` | XcodeGen spec and the generated project (committed so XcodeGen is optional). |

## Sync rules implemented

- A page (at most 200 samples, one request) is read with `HKAnchoredObjectQuery`, mapped,
  encoded deterministically and **persisted as pending together with its candidate anchor**
  before any upload. The committed anchor advances only after the server confirms the page.
  Timeouts, 401, 409, 422 and 5xx keep the pending page; the next sync replays the identical
  bytes and the server's replay detection answers with duplicates.
- Sync state is keyed by server URL + authenticated user id (from `/v1/me`) + HealthKit type +
  the consented history start + query configuration version; consent is bound to server and
  user. Changing server, account or history range never reuses another scope's queue or
  anchor. Granting consent with a different history range discards pending pages read under
  the previous range (they are never replayed) and starts the new range from a fresh anchor, so
  widening the range re-reads history and the server answers with duplicates.
- The verified identity is bound to the exact server URL and credential version that produced
  it (`IdentitySession`). Editing the server URL or saving a token clears it immediately,
  cancels any running sync, and makes a late `/v1/me` response for the old pair be ignored.
  Consent can only be granted, and a sync only started, with an identity verified against the
  current server and token.
- A sync runs on a frozen context (server, user, token, consent captured at start). Revoking
  consent, changing the token or server, or connecting as a different user cancels the run
  before its next device read or request. A request already in flight cannot be recalled; its
  page stays pending and is subject to the next consent's rules.
- The HealthKit predicate uses `.strictStartDate`, so only samples that **start** at or after
  the consented history start are read; the engine re-checks the bound before encoding.
- Paging ends on the query's own signal: HealthKit's `limit` counts returned samples and deleted
  objects together, so the page count includes deletions and is never the filtered sample count.
  Pages made only of in-bed sleep samples or only of deletions do not end a sync early.
- Samples the mapper rejects (for example an invalid time-zone metadata value) are recorded per
  scope with their UUID and reason, shown in the sync view, and excluded from the upload; they
  are never silently dropped.
- State files written by earlier builds decode with safe defaults; scopes from an earlier
  query-configuration version are left in place and simply not used.
- `source_record_id` is `HKSample.uuid`. Values are read with explicit `HKUnit`s and formatted
  with at most six decimals. Samples without `HKMetadataKeyTimeZone` use the fixed `UTC`
  fallback, so a replay from another device time zone cannot turn into a 409.
- Sleep: only asleep states (unspecified, core, deep, REM) are uploaded, one `interval_total`
  per stage sample with the asleep seconds derived from the sample interval on the device.
  In-bed and awake samples are not uploaded. The server sums stages per start day and flags
  overlaps; it does not reproduce Apple's source priority, so the result is "summed asleep
  samples", not "last night's total sleep".
- HealthKit deletions are counted persistently per scope and shown as "deleted upstream, not
  synced". The server has no delete sync yet.
- `provider = healthkit` is the acquisition channel. `source_device` is the HealthKit source or
  device label; Simulator samples are labelled with this app's name, never as a watch.

## Build and run (Simulator)

Prerequisites: Xcode 26 with an iOS 26 simulator, the local stack from the repository root
(`docker compose up -d`), and a development token for a dedicated synthetic user:

```bash
PYTHONPATH=services/ingestion-api/src uv run healthops create-user --name ios-synthetic-demo --output .local/ios-demo-credentials.json
```

```bash
cd ios/HealthOpsUploader && swift test
```

```bash
cd ios/HealthOpsUploader && xcodebuild -project HealthOpsUploader.xcodeproj -scheme HealthOpsUploaderApp -destination 'platform=iOS Simulator,name=iPhone 17' -configuration Debug -derivedDataPath "$TMPDIR/HealthOpsDerivedData" build
```

Use a DerivedData path outside the cloud-synced Desktop folder: on this Mac, files under
`~/Desktop` receive Finder/file-provider extended attributes that make `codesign` fail with
"resource fork, Finder information, or similar detritus not allowed".

In the app: paste the token (it is stored in the Keychain), connect to `http://127.0.0.1:8000`
(the Simulator shares the Mac's loopback interface), request HealthKit authorization, write
synthetic samples (debug builds only), grant consent, then sync. Only the Debug configuration
allows plain HTTP, and only to local networking (`NSAllowsLocalNetworking`); Release keeps
App Transport Security intact. `appleExerciseTime` cannot be written by third-party apps, so
`exercise_duration` has no Simulator end-to-end path; its mapping is unit-tested only.

Regenerate the project after editing `project.yml` with `xcodegen generate`; keep the HealthKit
entitlement in `project.yml`, because XcodeGen rewrites the entitlements file.
