# Provenance and scope

This repository preserves the earlier standalone HealthOps history and integrates selected
application work from [HuihanCui/ac215_Megatron](https://github.com/HuihanCui/ac215_Megatron)
at commit `c23565d63b0236f969ea4672893a6b0784226974`. New commits are functional migration batches, not a reconstruction
of when the underlying implementation was originally written. Source files may receive path,
publication and documentation adaptations; behavior changes are identified separately.

Zhentian Wu's application work covers ingestion/authentication, deterministic tools, the
assistant and evaluation, reference retrieval, the iOS client and application deployment.
The source project's [contribution table](https://github.com/HuihanCui/ac215_Megatron/blob/c23565d63b0236f969ea4672893a6b0784226974/contributions-table.md)
records the team split. Huihan Cui and Helena Li contributed the data generation, processing,
versioning and device-reconciliation training pipeline; that pipeline remains in the team
repository. The service directory convention follows the team's course-template migration.
This portfolio does not claim sole authorship of the team project or of third-party libraries.

Included: application code and tests, synthetic fixture generators and question sets,
knowledge-source manifests, client code, deployment templates and selected evidence.
Excluded: dataset contents, DVC stores, OAuth/app credentials, personal health records,
private cloud configuration, raw evaluation responses and local Chinese learning notes.
No new license is asserted for upstream or third-party work. Knowledge source permissions
and quote limits remain recorded in `knowledge/sources.yaml`.

The small public synthetic fixture is reproducible without the team's private data store.
The exact three-cohort evaluation additionally requires the versioned synthetic cohort;
its reported results must not be presented as newly reproduced by that small fixture.
