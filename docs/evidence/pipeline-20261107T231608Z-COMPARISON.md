## transformer: w14 vs w30

- **w14**: window=14, epochs=30, d_model=64, layers=2, stride=1; fit 202.4 s; W&B https://forge.coreweave.com/wandb/helenaayzli-harvard-university/healthops-m3/runs/7331sv3j; `/gcs/ac215-project-509705-healthops-data/pipelines/runs/20261107T231608Z/w14`
- **w30**: window=30, epochs=30, d_model=64, layers=2, stride=1; fit 166.5 s; W&B https://forge.coreweave.com/wandb/helenaayzli-harvard-university/healthops-m3/runs/xumnr6fr; `/gcs/ac215-project-509705-healthops-data/pipelines/runs/20261107T231608Z/w30`

### Test users, overall

| run | metric | n | MAE | coverage90 | width90 | attribution |
|---|---|---|---|---|---|---|
| apple_only | sleep (min) | 21857 | 20.52 |  |  | 0.43 |
| apple_only | steps (steps) | 26548 | 1502.84 |  |  | 0.39 |
| apple_only | rhr (bpm) | 24317 | 1.37 |  |  | 0.64 |
| fitbit_only | sleep (min) | 17880 | 17.24 |  |  | 0.57 |
| fitbit_only | steps (steps) | 23641 | 972.39 |  |  | 0.61 |
| fitbit_only | rhr (bpm) | 22477 | 2.14 |  |  | 0.36 |
| device_average | sleep (min) | 35116 | 18.61 |  |  | 0.44 |
| device_average | steps (steps) | 42787 | 1209.43 |  |  | 0.46 |
| device_average | rhr (bpm) | 40354 | 1.69 |  |  | 0.56 |
| global_correction | sleep (min) | 35116 | 13.00 |  |  | 0.84 |
| global_correction | steps (steps) | 42787 | 1250.89 |  |  | 0.45 |
| global_correction | rhr (bpm) | 40354 | 1.21 |  |  | 0.89 |
| w14 | sleep (min) | 35116 | 12.17 | 0.93 | 49.91 | 0.83 |
| w14 | steps (steps) | 42787 | 986.92 | 0.91 | 4411.25 | 0.75 |
| w14 | rhr (bpm) | 40354 | 1.18 | 0.93 | 5.29 | 0.89 |
| w30 | sleep (min) | 35116 | 12.13 | 0.88 | 44.90 | 0.84 |
| w30 | steps (steps) | 42787 | 1006.26 | 0.90 | 4316.24 | 0.73 |
| w30 | rhr (bpm) | 40354 | 1.18 | 0.89 | 4.72 | 0.88 |

### By cohort

| run | metric | cohort | n | MAE | coverage90 | width90 |
|---|---|---|---|---|---|---|
| w14 | sleep | apple_watch_only | 15149 | 11.49 | 0.93 | 45.81 |
| w14 | sleep | fitbit_only | 12052 | 13.55 | 0.94 | 55.60 |
| w14 | sleep | paired | 7915 | 11.36 | 0.93 | 49.09 |
| w14 | steps | apple_watch_only | 18414 | 1336.42 | 0.92 | 6145.65 |
| w14 | steps | fitbit_only | 15928 | 771.86 | 0.89 | 3192.29 |
| w14 | steps | paired | 8445 | 630.44 | 0.93 | 2928.50 |
| w14 | rhr | apple_watch_only | 16864 | 1.07 | 0.93 | 4.83 |
| w14 | rhr | fitbit_only | 15138 | 1.39 | 0.93 | 6.23 |
| w14 | rhr | paired | 8352 | 1.01 | 0.92 | 4.53 |
| w30 | sleep | apple_watch_only | 15149 | 11.45 | 0.90 | 41.88 |
| w30 | sleep | fitbit_only | 12052 | 13.55 | 0.84 | 49.06 |
| w30 | sleep | paired | 7915 | 11.27 | 0.89 | 44.33 |
| w30 | steps | apple_watch_only | 18414 | 1352.15 | 0.91 | 5996.26 |
| w30 | steps | fitbit_only | 15928 | 785.93 | 0.87 | 3062.99 |
| w30 | steps | paired | 8445 | 667.63 | 0.93 | 3016.73 |
| w30 | rhr | apple_watch_only | 16864 | 1.07 | 0.89 | 4.28 |
| w30 | rhr | fitbit_only | 15138 | 1.38 | 0.89 | 5.60 |
| w30 | rhr | paired | 8352 | 1.03 | 0.88 | 4.03 |

### By history length

| run | metric | history | n | MAE | coverage90 | width90 |
|---|---|---|---|---|---|---|
| w14 | sleep | days_0_6 | 4218 | 12.00 | 0.93 | 50.11 |
| w14 | sleep | days_7_29 | 13573 | 12.10 | 0.94 | 49.89 |
| w14 | sleep | days_30_plus | 17325 | 12.26 | 0.93 | 49.87 |
| w14 | steps | days_0_6 | 5098 | 1005.34 | 0.90 | 4231.62 |
| w14 | steps | days_7_29 | 16579 | 986.86 | 0.91 | 4440.78 |
| w14 | steps | days_30_plus | 21110 | 982.51 | 0.91 | 4431.43 |
| w14 | rhr | days_0_6 | 4797 | 1.19 | 0.93 | 5.30 |
| w14 | rhr | days_7_29 | 15641 | 1.18 | 0.93 | 5.29 |
| w14 | rhr | days_30_plus | 19916 | 1.17 | 0.93 | 5.29 |
| w30 | sleep | days_0_6 | 4218 | 12.05 | 0.87 | 44.86 |
| w30 | sleep | days_7_29 | 13573 | 12.11 | 0.88 | 44.86 |
| w30 | sleep | days_30_plus | 17325 | 12.17 | 0.87 | 44.93 |
| w30 | steps | days_0_6 | 5098 | 1027.41 | 0.88 | 4184.81 |
| w30 | steps | days_7_29 | 16579 | 1008.98 | 0.90 | 4354.39 |
| w30 | steps | days_30_plus | 21110 | 999.02 | 0.90 | 4318.01 |
| w30 | rhr | days_0_6 | 4797 | 1.18 | 0.89 | 4.81 |
| w30 | rhr | days_7_29 | 15641 | 1.18 | 0.89 | 4.73 |
| w30 | rhr | days_30_plus | 19916 | 1.17 | 0.89 | 4.70 |
