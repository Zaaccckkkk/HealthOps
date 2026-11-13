# Data Collector

Downloads the real wearable datasets into a raw data version, verifies every file against its
publisher's checksum and writes the raw manifest.

| | |
|---|---|
| Input | NetHealth Fitbit files from Zenodo (record 21904040); BIDSleep Apple Watch nights from PhysioNet (v1.0.1); Synthea output already in the same raw version |
| Output | `data/raw/<OUTPUT_DATA_VERSION>/{nethealth,bidsleep}/` and `data/manifests/<OUTPUT_DATA_VERSION>.yaml` |
| Environment | `OUTPUT_DATA_VERSION` (`raw-*`), `BIDSLEEP_INCLUDE_MOTION` (default `false`), `DOWNLOAD_WORKERS` (default 8), `DATA_ROOT`, `GIT_COMMIT` |

- NetHealth: the Zenodo API lists each file with its MD5; both Fitbit CSVs (about 56 MB) are
  downloaded and checked.
- BIDSleep: `SHA256SUMS.txt` lists every file; `hr.csv` and `labels.mat` of all 253 nights
  (about 40 MB) are downloaded and checked. `motion.csv` (about 28 GB) only with
  `BIDSLEEP_INCLUDE_MOTION=true`.
- Already verified files are not downloaded again, so an interrupted run can be resumed.
  Partial downloads are written to `*.part` and renamed only after the checksum matches.

```bash
docker compose --profile data run --rm data-collector        # from the repository root
```

Code: `collector/sources.py` (sources, download, verification), `collector/cli.py` (entry point),
`collector/versioning.py` (shared version/manifest helpers, identical in every data container).
