# Provenance and scope

This repository preserves the standalone HealthOps history and synchronizes the published
[AC215 team project](https://github.com/HuihanCui/ac215_Megatron) `milestone3` branch at
commit [`65852283a20c5528403b611e4a78ef6135856e6a`](https://github.com/HuihanCui/ac215_Megatron/commit/65852283a20c5528403b611e4a78ef6135856e6a).
The earlier application-only import used `c23565d63b0236f969ea4672893a6b0784226974`.
A November 13 update also imports 26 owner-authorized local working-tree files based on
that same team commit; these changes had not yet been published on the team branch. See the
[follow-up synchronization record](evidence/portfolio-sync-20261113.md) for the source snapshot
and validation. Synchronization is organized into functional migration commits; their dates
record this import, not when the underlying team implementation was originally written.

## Authorship

Zhentian Wu's application work covers ingestion/authentication, deterministic tools,
assistant orchestration and evaluation, reference retrieval, the iOS client, and application
deployment, monitoring, and Writer post-training/evaluation tooling. Huihan Cui and Helena Li
contributed the data generation, processing, versioning, and cross-device model-training
pipeline. The [contribution table](../contributions-table.md) distinguishes project direction
from implementation. The owner clarified on November 13 that the original idea, template proposal
and setup direction are Zhentian Wu's; teammates retain credit for the corresponding migration
and setup implementation. Dataset selection and licence review are credited to Huihan Cui
and Helena Li. Data-related work is listed after application and infrastructure work.
Shared components and team-developed pipelines are included to reproduce the complete project;
this portfolio does not claim sole authorship of the team project or third-party libraries.
No new license is asserted for upstream or third-party work.

## Synchronization boundary

Included: all upstream tracked source, tests, dependency locks, deployment templates,
configuration examples, data manifests/DVC pointers, and published documentation/evidence,
except the root README. The November 12 synchronization preserved the portfolio README and
its demo assets byte for byte. On November 13, the README's implementation summary, code map
and authorship scope were updated in the existing portfolio format, and its pipeline figure
was switched to the supplied simple PNG. The later November 13 update retains the demo and
figure, refreshes implementation details and contributions, and replaces the README evaluation
summary with the owner-supplied latest aggregate results. Historical run reports remain intact;
they are not relabeled as evidence for the new aggregate. See the
[results source note](evidence/portfolio-results-20261113.md).

Portfolio-specific additions remain: the demo, quickstart, previous migration record,
assistant workflow illustration, and this provenance file. Local artifact ignore rules and
`make test` are retained. See the [Milestone 3 synchronization record](evidence/milestone3-sync-20261112.md)
for the differences, adaptations, and checks actually performed for this import.

Dataset contents and model weights are not copied from external stores. The upstream's small
published synthetic evidence samples remain synthetic examples. Credentials, personal health
records, downloaded corpora, raw private artifacts, and local Chinese learning notes are
excluded. DVC and cloud templates reference upstream resources; accessing them requires the
owner's own credentials and permissions. Synchronizing code does not deploy or train anything.

Upstream evidence documents report their original runs and dates. They are not new validation
results from this synchronization. Exact cohort evaluations require the versioned upstream
synthetic data; the public quickstart fixture is not a reproduction of those cohort scores.
Knowledge-source permissions remain in `knowledge/sources.yaml`.
