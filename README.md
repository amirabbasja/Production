# Distributed Neural Network Training Worker

This repository contains a GPU worker used for distributed neural network training
on cloud CUDA hosts. The notebook (`train.ipynb`) is the primary entry point when
running on hosted notebook platforms (Kaggle, Colab, and similar); `train.py` is
the CLI entry point wrapping the same code path for long-lived boxes.

The worker joins a coordinated training cluster operated by us ("WR"):

1. On startup it loads `config.json`, which holds the coordinator endpoint
   (`S3.host` / `S3.port`), the worker identity, and the training schedule.
2. A site VPN link (`vpn.link`, handled by the bundled `wr-gateway` helper)
   routes the trainer's traffic through our private network before anything
   else touches the network.
3. The trainer authenticates against the coordinator with the session login and
   begins pulling *training batches* — GPU-sized blocks of a much larger
   int8 (DP4A) matrix multiplication that make up one distributed training step.
4. Each completed batch is cryptographically attested (commitment trees over the
   operands, digest transcripts per output tile) so the coordinator can verify
   every worker's contribution before accepting it, like any verifiable-
   computation distributed training stack.
5. Progress is reported live in the notebook status line (`job-N | GPU% |
   time left | rate`) and optionally to Telegram.

The training schedule (`training.job_sequence`) alternates between two kinds of
effort per user configuration:

- **job-0**: main distributed-training matmul effort, capped by a configured
  `max_gpu_utilization` duty-cycle target so hosts stay within their quota.
- **job-1**: optional secondary fine-tuning pass (`side_job`) on a Hugging Face
  image-classification dataset, run during scheduled cooldown intervals.

## Usage

Notebook: open `train.ipynb`, keep the cells in order (environment check →
dependency install → VPN health check → start training). Stop by interrupting the
training cell.

CLI:

```bash
python train.py                 # normal run (uses ./config.json)
python train.py --login-only    # authenticate once, verify connectivity, exit
```

## Layout

| Path | Purpose |
| --- | --- |
| `train.py`, `train.ipynb` | Entry points / process supervision |
| `config.json` | Coordinator endpoint, worker identity, schedule, VPN link |
| `src/workrunner/` | Trainer runtime (protocol, sessions, throttling, telemetry) |
| `src/workrunner/cuda/` | Native CUDA kernels (int8 DP4A batch compute) compiled and cached at first use |
| `src/work_helpers/` | Pinned helper bundle used to attest training submissions |

Configuration notes: most operators will copy a prepared `config.json` provided by
the training coordinator operator. Set `telegram.*` credentials to receive training
progress messages on your phone while the job runs unattended.
