# gleaner

**gleaner runs your batch jobs on the CPU you already pay for and don't use.**

Most EC2 instances sit at 10 to 30 % CPU. The rest is paid for and wasted. gleaner puts a small agent on those machines, measures the idle capacity, and fills it with tasks: resizing images, converting files, any short Python job. The client's own application always comes first.

You describe the task in plain English. An LLM writes the code, a teammate reviews it, and gleaner spreads it over the idle machines with the smallest IAM rights possible.

Built for the Meta hackathon (October 27 to November 4, 2026), with Meta's LLM `muse-spark-1.3`.

## The demo

1. An engineer opens the web interface and writes: *"Resize every photo to 800 px wide, keep the proportions, save the result as JPEG in the resized/ folder of the same bucket."* They give the folder to read (`s3://bucket/photos/`) and the folder to write (`s3://bucket/resized/`).
2. The LLM writes the Python code of the task and picks an IAM role from the roles this engineer is allowed to use.
3. A second engineer reviews the code and the role, then approves. The author can never approve their own task.
4. core lists the photos and splits them into batches of 20, one SQS message per batch. Each message carries the code, the role and the file names.
5. The agents on the client's EC2s pick up the batches when the machine is idle and run the code in a locked-down container.
6. The resized photos appear in `resized/`, and the interface shows each machine's load and the compute wasted.

Tested end to end on AWS on November 2: 25 photos, 2 batches, 25 resized photos, nothing in the dead-letter queue.

## How a task travels

```
 web interface                      core (FastAPI, api.talsi.dev)
 ─────────────                      ─────────────────────────────
 description + resources ─────────▶ POST /task
                                      ├─ LLM picks the IAM role (closed list)
                                      ├─ LLM writes the Python code
                                      └─ status "a_valider", nothing sent yet
 reviewer approves ───────────────▶ POST /tasks/{id}/validate
                                      ├─ lists the input files on S3
                                      └─ 1 SQS message per batch of 20 files
                                                   │
 client's EC2                                      ▼
 ────────────                                  SQS queue
 agent ── heartbeat every 5 s ──▶ core               │
       ◀── order: start / stop ───                   │
 worker ◀───────────────────────────────────────────┘
   ├─ sts:AssumeRole on the task's role, limited to the declared folders
   └─ docker run python -c <code>, as nobody, with CPU and memory limits
                                                   │
                                                   ▼
                                     results written to S3
```

core never calls the agent. The agent opens every connection, so the client opens no inbound port.

## Least privilege, at every step

The code runs on the client's machines and was written by an LLM. gleaner treats it as untrusted from start to finish:

| Step | Who | What limits the damage |
|---|---|---|
| Who may grant what | admin | Each person has a list of allowed IAM roles, taken from the client's closed list. |
| Role choice | LLM | It only sees the author's allowed roles, and core rejects any other answer. |
| Review | another person | Mandatory four-eyes review of the code and the role before anything runs. |
| Execution | worker | The role is assumed for 15 minutes with a session policy: `s3:GetObject` on the input folders, `s3:PutObject` on the output folders, nothing else. Even malicious code can't overwrite the originals or read elsewhere. |
| Container | Docker | The code runs as `nobody` and never sees the EC2's own credentials (metadata hop limit of 1). |

## The client's application comes first

- **core only starts work when the machine is quiet.** It starts when the client uses less than 70 % of the CPU and stops above 90 %. In between, nothing changes, so a small spike doesn't kill a task. 30 % of the CPU and 512 MB of memory are always left to the client.
- **The kernel always favors the client.** The task container gets the lowest CPU priority (`cpu_shares=2`), and the kernel kills it first if memory runs out (`oom_score_adj=1000`).
- **Stopping costs nothing.** The agent kills the task on a stop order, or when core has been silent for 15 seconds. The SQS message comes back after the visibility timeout, and the LLM is asked for code that can resume at any moment: the demo's code skips the photos already resized.

## Repository

| Folder | What it holds |
|---|---|
| `agent/ec2/` | The agent (`agent.py`: heartbeats and orders) and the worker (`worker.py`: SQS, AssumeRole, Docker). Its image also runs the tasks: Python 3.12, boto3, Pillow. |
| `core/ec2/` | The core API (`task_manager_api.py`), the LLM calls and batching (`llm.py`), people and roles (`users.py`), the scheduler, the waste report, and the web interface (`index.html`). |
| `core/infra/` | Terraform for the test environment: SQS queue and DLQ, task roles, photo bucket, 2 agent EC2s. |
| `core/hosting/` | Terraform for the EC2 that hosts core behind Caddy (HTTPS with Let's Encrypt). |
| `core/registry/` | Terraform for the public ECR repository of the agent image. |
| `docs/` | `architecture.md` (how the code works) and `plan.md` (design decisions and schedule), in French. |

## Run it

You need an AWS account, Terraform, Docker, and a Meta API key in `core/ec2/.env` (`meta_api_key=...`, ignored by git).

**1. Publish the agent image** (once, or after a change in `agent/ec2/`):

```bash
terraform -chdir=core/registry apply
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws
docker buildx build --platform linux/amd64 -t public.ecr.aws/<alias>/gleaner-agent:v0.2 --push agent/ec2
```

**2. Create the queue, the roles, the bucket and the agent EC2s:**

```bash
terraform -chdir=core/infra apply -var core_url=https://api.talsi.dev
```

**3. Deploy core**, with the Meta key loaded from `.env`:

```bash
set -a; source core/ec2/.env; set +a
export TF_VAR_meta_api_key="$meta_api_key"
terraform -chdir=core/hosting apply
terraform -chdir=core/hosting output -raw core_ip
```

Point the DNS A record of your domain to this IP (DNS only, no proxy: Caddy needs port 80 for its certificate). core answers on HTTPS about 2 minutes later.

**4. Add photos** to `s3://gleaner-task-test-<account id>/photos/`, open the web interface and sign in. The demo users are in `core/ec2/users.py`: `engineer_1` creates the task, `engineer_2` approves it, `admin` manages the roles.

**5. Destroy everything** when you're done:

```bash
terraform -chdir=core/hosting destroy -var meta_api_key=unused
terraform -chdir=core/infra destroy -var core_url=unused
```

**Run core locally:** `pip install -r requirements.txt`, then from `core/ec2/`: `AWS_DEFAULT_REGION=us-east-1 QUEUE_URL=<queue url> meta_api_key=<key> uvicorn task_manager_api:app`.

## Hackathon shortcuts

What we knowingly left for after the hackathon:

- **Everything is in memory.** Tasks, people and measurements are lost when core restarts.
- **Keys are hardcoded.** User keys are in `users.py`, and all agents share one key.
- **The Meta key travels in the EC2's user_data** and in the Terraform state. It will be rotated after the hackathon.
- **The reviewer can't change the role yet.** They can only approve the one the LLM picked.
- **One queue for every task, without priorities.** core lists at most 1000 files per input folder.
- **Some API error messages are still in French.**
