# Deploying the demo on AWS Academy (Learner Lab)

Operational guide to bring up the monitoring demo on an EC2 instance inside **AWS Academy Learner Lab**. This guide complements the [Public deployment for the class poster](../README_EN.md#public-deployment-for-the-class-poster) section of the README — here we cover everything specific to the Academy environment (4-hour session, fixed region, `vockey` keypair, instance-type limits, credits), and reuse the `make poster-fresh` flow already documented for stack startup.

## Index
- [AWS Academy Learner Lab specifics](#aws-academy-learner-lab-specifics)
- [Recommended resources](#recommended-resources)
- [Fast path: steps 3–7 from AWS CLI](#fast-path-steps-37-from-aws-cli)
- [Step 1 — Start the Lab and open the console](#step-1--start-the-lab-and-open-the-console)
- [Step 2 — Download the SSH key (`labsuser.pem`)](#step-2--download-the-ssh-key-labsuserpem)
- [Step 3 — Create the security group](#step-3--create-the-security-group)
- [Step 4 — Launch the EC2 instance](#step-4--launch-the-ec2-instance)
- [Step 5 — (Recommended) Allocate an Elastic IP](#step-5--recommended-allocate-an-elastic-ip)
- [Step 6 — Connect via SSH](#step-6--connect-via-ssh)
- [Step 7 — Install Docker, Compose, git, and make](#step-7--install-docker-compose-git-and-make)
- [Step 8 — Clone the repo and configure `.env.poster`](#step-8--clone-the-repo-and-configure-envposter)
- [Step 9 — Bring up the stack](#step-9--bring-up-the-stack)
- [Step 10 — Verify the 6 public URLs](#step-10--verify-the-6-public-urls)
- [Day-to-day operation](#day-to-day-operation)
- [Troubleshooting](#troubleshooting)
- [Tear down everything when done](#tear-down-everything-when-done)

## AWS Academy Learner Lab specifics

Before starting, keep in mind what changes from a regular AWS account:

- **4-hour session.** Each time you enter the Lab you have to press **Start Lab** and the session lasts ~4 h. When it expires, AWS stops charging you credits but the EC2 instance stays **stopped/running depending on how you left it** — resources are not deleted. When you come back, press **Start Lab** again and the console becomes accessible.
- **Fixed region.** The lab typically only allows **us-east-1 (N. Virginia)**. Don't try to switch regions — many services are blocked outside the lab's region.
- **Only `LabRole`.** You can't create new IAM roles. The instance launches with the pre-existing `LabInstanceProfile` role if you want AWS permissions from the VM (not needed for the demo).
- **Provided keypair: `vockey`.** The Learner Lab ships with a keypair called `vockey`. The private key (`labsuser.pem`) is downloaded from the **AWS Details** panel of the Lab. No need to create a new keypair — use `vockey` directly when launching the instance.
- **Limited credits** ($50 USD per course, no more). A `t3.large` running 24/7 costs ~$60/month (≈ $0.0832/h × 730 h) — $50 buys you about **600 hours = ~25 days** of continuous uptime. **Always stop the instance** when not using it. Stopping does NOT delete the disk, just stops compute charges.
- **Limited instance types.** Vocareum usually allows up to `t2.large` / `t3.large` (8 GB RAM). Larger ones (`xlarge`, `2xlarge`) are blocked.
- **Elastic IP: up to 1 allowed.** Important so the poster URL doesn't change when you stop and start the instance.

## Recommended resources

To run the full stack + Caddy (public mode / 11 services, ~2.7 GB RAM per [Demo modes](../README_EN.md#demo-modes)):

| Resource | Recommended value | Minimum viable |
|---|---|---|
| Instance type | `t3.large` (2 vCPU, 8 GB RAM) | `t3.medium` (4 GB) — only if you scale down to [3-service minimal mode](../README_EN.md#minimal-local-low-ram) |
| AMI | Ubuntu Server 22.04 LTS (HVM, x86_64) | Amazon Linux 2023 also works but the commands below assume Ubuntu |
| EBS disk | 30 GiB gp3 | 20 GiB |
| Region | us-east-1 (only available) | — |

`t3.medium` with the full stack will swap heavily and Elasticsearch may OOM. Go directly to `t3.large` and plan to stop it when not in use so the $50 credit lasts.

## Fast path: steps 3–7 from AWS CLI

If you have AWS CLI configured with the Lab's temporary credentials (panel **AWS Details → AWS CLI** → copy the `[default]` block to `~/.aws/credentials`), you can skip the console and run steps 3–7 with a single script. The console is still needed to download `labsuser.pem` (Step 2). Docker/Compose bootstrap is done via **user-data**, so by the time you SSH in everything is already installed and there's no need for the logout/login cycle for the `docker` group to take effect.

```bash
# Region (the only one allowed in Learner Lab)
aws configure set region us-east-1

# Your public IP, to restrict SSH
MY_IP=$(curl -s https://checkip.amazonaws.com)
DEFAULT_VPC=$(aws ec2 describe-vpcs \
  --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)

# 1) Security group + ingress rules
SG_ID=$(aws ec2 create-security-group \
  --group-name monitoring-demo-sg \
  --description "Monitoring demo: SSH from owner, HTTP/HTTPS + fallback ports public" \
  --vpc-id "$DEFAULT_VPC" \
  --query GroupId --output text)

aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions "[
  {\"IpProtocol\":\"tcp\",\"FromPort\":22,\"ToPort\":22,\"IpRanges\":[{\"CidrIp\":\"$MY_IP/32\",\"Description\":\"SSH from owner\"}]},
  {\"IpProtocol\":\"tcp\",\"FromPort\":80,\"ToPort\":80,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\",\"Description\":\"HTTP / Caddy ACME\"}]},
  {\"IpProtocol\":\"tcp\",\"FromPort\":443,\"ToPort\":443,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\",\"Description\":\"HTTPS / Caddy\"}]},
  {\"IpProtocol\":\"tcp\",\"FromPort\":3000,\"ToPort\":3000,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\",\"Description\":\"Grafana fallback\"}]},
  {\"IpProtocol\":\"tcp\",\"FromPort\":9090,\"ToPort\":9090,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\",\"Description\":\"Prometheus fallback\"}]},
  {\"IpProtocol\":\"tcp\",\"FromPort\":8080,\"ToPort\":8080,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\",\"Description\":\"PanicDuty fallback\"}]},
  {\"IpProtocol\":\"tcp\",\"FromPort\":5601,\"ToPort\":5601,\"IpRanges\":[{\"CidrIp\":\"0.0.0.0/0\",\"Description\":\"Kibana fallback\"}]}
]"

# 2) User-data that installs Docker + Compose on first boot
cat > /tmp/userdata.sh <<'EOF'
#!/bin/bash
set -eux
exec > /var/log/bootstrap.log 2>&1
apt-get update
apt-get install -y ca-certificates curl gnupg git make
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
usermod -aG docker ubuntu
systemctl enable --now docker
touch /var/log/bootstrap-done
EOF

# 3) Latest Ubuntu 22.04 AMI (owner = Canonical)
AMI_ID=$(aws ec2 describe-images \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
            "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)

# 4) Launch instance (vockey already exists in Learner Lab)
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.large \
  --key-name vockey \
  --security-group-ids "$SG_ID" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=monitoring-demo}]' \
  --user-data file:///tmp/userdata.sh \
  --query 'Instances[0].InstanceId' --output text)

# 5) Elastic IP — allocate and associate when the instance is running
EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC"

EIP=$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" \
  --query 'Addresses[0].PublicIp' --output text)

cat <<INFO
Done:
  Instance:        $INSTANCE_ID
  EIP:             $EIP
  EIP-with-dashes: ${EIP//./-}
  Security group:  $SG_ID
INFO
```

The user-data runs for ~1–2 min while the instance initializes. To verify it finished (after downloading `labsuser.pem` per [Step 2](#step-2--download-the-ssh-key-labsuserpem)):

```bash
ssh -i /path/to/labsuser.pem ubuntu@$EIP \
  'ls /var/log/bootstrap-done && docker version --format "{{.Server.Version}}"'
```

If you see `/var/log/bootstrap-done` and the Docker server version, you're set — skip directly to [Step 8](#step-8--clone-the-repo-and-configure-envposter).

> **Why user-data and not install manually via SSH:** the `docker` group is applied to the `ubuntu` user *before* you open your first SSH shell, so there's no need for the manual Step 7's `exit` + reconnect. And `apt-get install` runs in parallel with the instance's other initialization — saves ~2 min total.

## Step 1 — Start the Lab and open the console

1. Go to **AWS Academy** → your course → **Modules** → **Learner Lab**.
2. Press **Start Lab** (button top-right). The circle next to it goes from red to yellow to **green** when ready (~1–2 min).
3. Press the green **AWS** circle to open the AWS console in a new tab.

> If the circle is red, the lab is off. If yellow, it's starting — wait.

## Step 2 — Download the SSH key (`labsuser.pem`)

1. In the Lab panel, press **AWS Details** (top-right).
2. Next to **SSH key**, press **Show** then **Download PEM**.
3. Save the file as `labsuser.pem` in a known location.
4. Restrict permissions (Linux/macOS):
   ```bash
   chmod 400 labsuser.pem
   ```

The same key is reused across all Lab instances, and you can re-download it as many times as you want from **AWS Details**.

## Step 3 — Create the security group

> If you ran the [AWS CLI fast path](#fast-path-steps-37-from-aws-cli), skip directly to [Step 8](#step-8--clone-the-repo-and-configure-envposter).

In the AWS console → **EC2** → **Security Groups** → **Create security group**:

- **Name:** `monitoring-demo-sg`
- **VPC:** the default one
- **Inbound rules:**

| Type | Port | Source | What for |
|---|---|---|---|
| SSH | 22 | **My IP** | connect from your laptop |
| HTTP | 80 | 0.0.0.0/0 | Caddy + Let's Encrypt (HTTP-01 challenge) |
| HTTPS | 443 | 0.0.0.0/0 | public traffic to Grafana/Prometheus/PanicDuty/Kibana via sslip.io subdomains |
| Custom TCP | 3000 | 0.0.0.0/0 | (optional, fallback) Grafana direct |
| Custom TCP | 9090 | 0.0.0.0/0 | (optional, fallback) Prometheus direct |
| Custom TCP | 8080 | 0.0.0.0/0 | (optional, fallback) PanicDuty direct |
| Custom TCP | 5601 | 0.0.0.0/0 | (optional, fallback) Kibana direct |

- **Outbound rules:** leave the default (everything allowed).

The 4 fallback ports (3000/9090/8080/5601) are optional but recommended if sslip.io takes time to resolve or Caddy doesn't issue a cert in time; you can swap the QRs to plain `http://<ip>:<port>` on the spot.

## Step 4 — Launch the EC2 instance

AWS console → **EC2** → **Launch instance**:

- **Name:** `monitoring-demo`
- **AMI:** *Ubuntu Server 22.04 LTS (HVM), SSD Volume Type* (the first one marked Free tier).
- **Instance type:** `t3.large` (or `t3.medium` if you'll use minimal mode).
- **Key pair:** `vockey` (the one provided by the lab).
- **Network settings → Edit:**
  - VPC: default.
  - Subnet: any from the default VPC.
  - **Auto-assign public IP: Enable.**
  - Firewall: **Select existing security group** → `monitoring-demo-sg`.
- **Configure storage:** 30 GiB, gp3.
- **Advanced details:** leave everything default. (No IAM instance profile needed for the demo, but if you want you can add `LabInstanceProfile` — doesn't break anything.)
- **Launch instance.**

Wait for the state to become **Running** and health checks to pass **2/2** (~1 min). Note the **Public IPv4 address** from the instance detail.

## Step 5 — (Recommended) Allocate an Elastic IP

Without an Elastic IP, every time you stop and start the instance (to save credits when not using it) **the public IP changes**, which invalidates the sslip.io hostnames and therefore the poster QRs.

To avoid this:

1. **EC2 → Elastic IPs → Allocate Elastic IP address → Allocate.**
2. Select the new EIP → **Actions → Associate Elastic IP address**.
3. Instance: `monitoring-demo` → **Associate**.

From now on the instance's public IP is the EIP — survives stop/start. **If you release the EIP without associating it to anything, AWS charges ~$3.60/mo** — when the presentation is done, first dissociate it and keep it associated until the demo ends, or release it together with the instance.

> Learner Lab typically allows **1 EIP per account**. If creation fails due to the limit, skip this step and live with the IP changing.

## Step 6 — Connect via SSH

From your local machine:

```bash
ssh -i labsuser.pem ubuntu@<PUBLIC_IP>
```

If you get `Permissions 0644 are too open` for `labsuser.pem`, run `chmod 400 labsuser.pem` and retry.

If the connection hangs, the most likely cause is the security group not having port 22 open from your IP — check EC2 → Security Groups → `monitoring-demo-sg`.

## Step 7 — Install Docker, Compose, git, and make

Once inside the instance (`ubuntu@ip-...`):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git make

# Official Docker repo (includes docker compose v2 as a plugin)
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker ubuntu
```

**Log out and back in** so the `docker` group change takes effect:

```bash
exit
ssh -i labsuser.pem ubuntu@<PUBLIC_IP>
```

Verify:

```bash
docker --version
docker compose version
```

## Step 8 — Clone the repo and configure `.env.poster`

```bash
git clone <YOUR_REPO_URL> tarea-mlp
cd tarea-mlp/monitoring_demo

cp .env.poster.example .env.poster
nano .env.poster   # or vim
```

Replace `YOUR-IP-WITH-DASHES` with the instance's public IP, dots replaced with dashes, in **all 6 hostnames** of the template. Example: if the IP is `54.165.12.30`, it becomes `54-165-12-30`:

```
GRAFANA_HOST=grafana.54-165-12-30.sslip.io
PROMETHEUS_HOST=prometheus.54-165-12-30.sslip.io
ALERTMANAGER_HOST=alertmanager.54-165-12-30.sslip.io
PANICDUTY_HOST=panicduty.54-165-12-30.sslip.io
API_HOST=api.54-165-12-30.sslip.io
KIBANA_HOST=kibana.54-165-12-30.sslip.io

GF_ANONYMOUS_ROLE=Viewer
GF_ADMIN_PASSWORD=<a-strong-password>
```

> **Important:** change `GF_ADMIN_PASSWORD` to something strong — Grafana is exposed to the internet.

## Step 9 — Bring up the stack

```bash
make poster-fresh
```

Equivalent to `docker compose --env-file .env.poster --profile poster down --rmi local --remove-orphans` followed by `... up -d --build`. It's the recommended target to start the demo: rebuilds the local images (`model_api`, `panic_duty`) and recreates all containers with fresh state (Elasticsearch/Kibana/Prometheus end up empty — they have no named volumes), but **preserves the named volume `caddy_data`** so the Let's Encrypt certs survive the rebuild. The first `poster-fresh` after deploying to a new IP takes ~3–5 min (downloads heavy images + issues LE certs for the first time); subsequent ones are ~30–60 s and reuse cached certs.

For quick iteration when only code changes (no image rebuild) you can use `make poster-up` — it's lighter. But `poster-fresh` no longer risks burning the Let's Encrypt rate limit, so running it several times in a row is safe.

Watch the progress with:

```bash
make poster-logs       # Ctrl+C to exit, doesn't stop the containers
make poster-status     # status table for the 11 services
```

Wait for `kibana-init` to show `Exit 0` and the rest as `Up` or `Up (healthy)`.

## Step 10 — Verify the 6 public URLs

> **Note:** the URLs below use `3-226-31-220` because that's the actual deployment IP at the time this guide was written. **Replace `3-226-31-220` with your own EIP in dashed format** (the same one you put in `.env.poster` in Step 8).

From your browser (ideally from your phone on mobile data, to confirm it's reachable from outside):

- [https://grafana.3-226-31-220.sslip.io](https://grafana.3-226-31-220.sslip.io) → `ML System Dashboard`.
- [https://prometheus.3-226-31-220.sslip.io](https://prometheus.3-226-31-220.sslip.io) → Prometheus UI.
- [https://alertmanager.3-226-31-220.sslip.io](https://alertmanager.3-226-31-220.sslip.io) → Alertmanager UI.
- [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io) → alerts UI (PanicDuty).
- [https://api.3-226-31-220.sslip.io/docs](https://api.3-226-31-220.sslip.io/docs) → Model API Swagger UI.
- [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover) → logs (Kibana Discover).

The first hit on each subdomain takes ~30 s while Caddy negotiates the Let's Encrypt cert. After that it's instant.

> If any URL doesn't resolve after 1 min, check [Troubleshooting](#troubleshooting).

### Useful URLs in this deployment

Complete map equivalent to section 3 *Acceso a cada servicio* of `descripcion_demo_en.md`. All tools go through Caddy (HTTPS with Let's Encrypt cert) in this deployment. The IP in dashed format is `3-226-31-220`.

| Service | Public URL |
|---|---|
| Grafana (home) | [https://grafana.3-226-31-220.sslip.io](https://grafana.3-226-31-220.sslip.io) |
| Grafana — ML System Dashboard | [https://grafana.3-226-31-220.sslip.io/d/ml-system](https://grafana.3-226-31-220.sslip.io/d/ml-system) |
| Prometheus (home) | [https://prometheus.3-226-31-220.sslip.io](https://prometheus.3-226-31-220.sslip.io) |
| Prometheus Alerts | [https://prometheus.3-226-31-220.sslip.io/alerts](https://prometheus.3-226-31-220.sslip.io/alerts) |
| Prometheus Targets | [https://prometheus.3-226-31-220.sslip.io/targets](https://prometheus.3-226-31-220.sslip.io/targets) |
| Alertmanager | [https://alertmanager.3-226-31-220.sslip.io](https://alertmanager.3-226-31-220.sslip.io) |
| PanicDuty | [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io) |
| API (Swagger UI) | [https://api.3-226-31-220.sslip.io/docs](https://api.3-226-31-220.sslip.io/docs) |
| API (health) | [https://api.3-226-31-220.sslip.io/health](https://api.3-226-31-220.sslip.io/health) |
| Kibana (home) | [https://kibana.3-226-31-220.sslip.io](https://kibana.3-226-31-220.sslip.io) |
| Kibana — Discover (logs) | [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover) |
| Kibana — ML Drift Investigation Dashboard | [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard) |

The 6 tools live behind `*.sslip.io` subdomains proxied by Caddy to the stack services (`grafana:3000`, `prometheus:9090`, `alertmanager:9093`, `panic_duty:8080`, `model_api:8000`, `kibana:5601`). Caddy issues a Let's Encrypt cert on the first hit to each subdomain (~30 s the first time, instant after).

## Day-to-day operation

### When you're done — **stop** (don't terminate)

From the AWS console → EC2 → select the instance → **Instance state → Stop instance**. Stopping pauses compute charges (the most expensive part) but preserves the disk, configuration, built Docker images, and the associated EIP. **Critical to not burn through Learner Lab credits.**

When you come back, **Start instance** and within ~1 min it's available again. SSH in, `cd tarea-mlp/monitoring_demo`, `make poster-up` (preserves volumes — Let's Encrypt certs are kept), and you're set. If you want a clean start wiping previous data, use `make poster-fresh` instead of `poster-up`.

### When the Lab session expires (every 4 h)

The Learner Lab circle goes back to red. The EC2 instance stays in whatever state you left it — if you left it running, it keeps running and consuming credits. To reopen the AWS console, press **Start Lab** again. The session is for *your access to the AWS console*, not for *the resources themselves*.

### If the public IP changed (you didn't use EIP, or changed the EIP)

```bash
nano .env.poster        # update the 6 hostnames with the new IP
make poster-fresh       # tear everything down and rebuild, forces re-issue of certs with the new hostnames
```

Caddy will issue new certs (~30 s per subdomain on the first hit).

## Troubleshooting

### `https://...sslip.io` doesn't load / gives `ERR_CONNECTION_REFUSED`

- Verify the security group has 80 and 443 open to `0.0.0.0/0`.
- Verify Caddy is up: `make poster-status` → should show `caddy` as `Up`.
- Check the logs: `docker logs caddy --tail 100`. Errors like `unable to authorize` or `connection refused` almost always mean port 80 closed in the security group.

### Caddy issues a cert but the browser says `NET::ERR_CERT_AUTHORITY_INVALID`

It issued a **staging** Let's Encrypt cert because you hit the production rate limit. Wait ~1 h for the rate limit to clear and retry (in Learner Lab, changing IPs isn't trivial — you only get 1 EIP). That typically happens when you restart Caddy 5+ times in a few minutes.

### `kibana-init` is stuck as `Exit 1` (didn't import the dashboard)

Non-fatal — the demo still works without the auto-provisioned `ML Drift Investigation` Kibana dashboard (2 Lens panels). To retry:

```bash
docker compose --env-file .env.poster --profile poster up -d --force-recreate kibana-init
```

### Insufficient memory / Elasticsearch restarting in a loop

- You're on `t3.medium` (4 GB) → switch to `t3.large` (stop instance → Actions → Instance settings → Change instance type → `t3.large` → start).
- Or scale down to [3-service minimal mode](../README_EN.md#minimal-local-low-ram) (no ELK, no alerts) and forget about Caddy/poster.

### `make: command not found`

`sudo apt-get install -y make`. (On Ubuntu 22.04 server some AMIs don't include it).

### `make poster-fresh` (or `make poster-up`) fails with `permission denied while trying to connect to the Docker daemon socket`

You didn't log out of SSH after `usermod -aG docker ubuntu`. Run `exit` and reconnect.

### URLs work but the poster QRs don't — I just moved to a different IP

Regenerate the QRs with the new IP, **or** preferably encode the QRs against a URL shortener (bit.ly) and change the redirect target — see [QR tips](../README_EN.md#qr-tips) in the README.

## Tear down everything when done

When the poster is over and you no longer need the demo:

1. **Bring down the stack:**
   ```bash
   make poster-down
   ```
2. **From the AWS console:**
   - EC2 → Instances → select `monitoring-demo` → **Instance state → Terminate instance** (this DOES delete the disk — irreversible).
   - EC2 → Elastic IPs → select the EIP → **Actions → Release Elastic IP address**. (Leaving the EIP unassociated costs ~$3.60/mo.)
   - EC2 → Security Groups → `monitoring-demo-sg` → **Delete** (optional, doesn't cost money).
3. **End Lab** in the Learner Lab panel to close the session cleanly.

