# Despliegue de la demo en AWS Academy (Learner Lab)

Guía operacional para levantar la demo de monitoreo en una instancia EC2 dentro de **AWS Academy Learner Lab**. Esta guía complementa la sección [Despliegue público para el póster de clase](../README_ES.md#despliegue-público-para-el-póster-de-clase) del README — acá se cubre todo lo específico del entorno de Academy (sesión de 4 h, región fija, par de claves `vockey`, límites de tipo de instancia, créditos), y se reutiliza el flujo `make poster-fresh` ya documentado para el arranque del stack.

## Índice
- [Particularidades de AWS Academy Learner Lab](#particularidades-de-aws-academy-learner-lab)
- [Recursos recomendados](#recursos-recomendados)
- [Vía rápida: pasos 3–7 desde AWS CLI](#vía-rápida-pasos-37-desde-aws-cli)
- [Paso 1 — Iniciar el Lab y abrir la consola](#paso-1--iniciar-el-lab-y-abrir-la-consola)
- [Paso 2 — Descargar la clave SSH (`labsuser.pem`)](#paso-2--descargar-la-clave-ssh-labsuserpem)
- [Paso 3 — Crear el security group](#paso-3--crear-el-security-group)
- [Paso 4 — Lanzar la instancia EC2](#paso-4--lanzar-la-instancia-ec2)
- [Paso 5 — (Recomendado) Asignar una Elastic IP](#paso-5--recomendado-asignar-una-elastic-ip)
- [Paso 6 — Conectarse por SSH](#paso-6--conectarse-por-ssh)
- [Paso 7 — Instalar Docker, Compose, git y make](#paso-7--instalar-docker-compose-git-y-make)
- [Paso 8 — Clonar el repo y configurar `.env.poster`](#paso-8--clonar-el-repo-y-configurar-envposter)
- [Paso 9 — Levantar el stack](#paso-9--levantar-el-stack)
- [Paso 10 — Verificar las 6 URLs públicas](#paso-10--verificar-las-6-urls-públicas)
- [Operación día a día](#operación-día-a-día)
- [Troubleshooting](#troubleshooting)
- [Desmontar todo al terminar](#desmontar-todo-al-terminar)

## Particularidades de AWS Academy Learner Lab

Antes de empezar, conviene tener presente lo que cambia respecto a una cuenta AWS normal:

- **Sesión de 4 horas.** Cada vez que entrás al Lab tenés que apretar **Start Lab** y la sesión dura ~4 h. Cuando se vence, AWS deja de cobrarte créditos pero la instancia EC2 sigue **stopped/running según como la dejaste** — los recursos no se borran. Al volver al lab, apretás **Start Lab** otra vez y la consola vuelve a estar accesible.
- **Región fija.** El lab típicamente solo permite **us-east-1 (N. Virginia)**. No intentes cambiar de región — varios servicios están bloqueados fuera de la región del lab.
- **Solo `LabRole`.** No podés crear roles IAM nuevos. La instancia se lanza con el rol pre-existente `LabInstanceProfile` si querés permisos AWS desde la VM (no hace falta para la demo).
- **Par de claves provisto: `vockey`.** El Learner Lab ya viene con un keypair llamado `vockey`. La clave privada (`labsuser.pem`) se descarga desde el panel **AWS Details** del Lab. No hace falta crear un keypair nuevo — usá `vockey` directamente al lanzar la instancia.
- **Créditos limitados** ($50 USD por curso, no más). Una `t3.large` corriendo 24/7 cuesta ~$60/mes (≈ $0.0832/h × 730 h) — con $50 te alcanza para **~600 horas = ~25 días** prendida sin parar. **Siempre detené la instancia** cuando no la estés usando. Detener (stop) NO borra el disco, solo deja de cobrar cómputo.
- **Tipos de instancia limitados.** Vocareum suele permitir hasta `t2.large` / `t3.large` (8 GB RAM). Más grandes (`xlarge`, `2xlarge`) están bloqueados.
- **Elastic IP: hasta 1 permitida.** Importante para que la URL del póster no cambie cuando pares y arranques la instancia.

## Recursos recomendados

Para correr el stack completo + Caddy (modo público / 11 servicios, ~2.7 GB RAM según [Modos de la demo](../README_ES.md#modos-de-la-demo)):

| Recurso | Valor recomendado | Mínimo viable |
|---|---|---|
| Tipo de instancia | `t3.large` (2 vCPU, 8 GB RAM) | `t3.medium` (4 GB) — sólo si bajás a [modo mínimo de 3 servicios](../README_ES.md#mínimo-local-poca-ram) |
| AMI | Ubuntu Server 22.04 LTS (HVM, x86_64) | Amazon Linux 2023 también funciona pero los comandos de abajo asumen Ubuntu |
| Disco EBS | 30 GiB gp3 | 20 GiB |
| Región | us-east-1 (única disponible) | — |

`t3.medium` con stack completo va a tener swap pesado y Elasticsearch puede caerse por OOM. Andá directo a `t3.large` y planificá detenerla cuando no la uses para que los $50 de crédito te alcancen.

## Vía rápida: pasos 3–7 desde AWS CLI

Si tenés AWS CLI configurado con las credenciales temporales del Lab (panel **AWS Details → AWS CLI** → copiar el bloque `[default]` a `~/.aws/credentials`), podés evitar la consola y ejecutar los pasos 3–7 con un solo script. La consola sigue siendo necesaria para descargar `labsuser.pem` (Paso 2). El bootstrap de Docker/Compose se hace por **user-data**, así que para cuando entrás por SSH ya está todo instalado y no hace falta el ciclo de logout/login para que tome el grupo `docker`.

```bash
# Región (única permitida en Learner Lab)
aws configure set region us-east-1

# Tu IP pública para restringir SSH
MY_IP=$(curl -s https://checkip.amazonaws.com)
DEFAULT_VPC=$(aws ec2 describe-vpcs \
  --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)

# 1) Security group + reglas de ingreso
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

# 2) User-data que instala Docker + Compose en el primer boot
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

# 3) AMI más reciente de Ubuntu 22.04 (owner = Canonical)
AMI_ID=$(aws ec2 describe-images \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
            "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)

# 4) Lanzar instancia (vockey ya existe en Learner Lab)
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.large \
  --key-name vockey \
  --security-group-ids "$SG_ID" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=monitoring-demo}]' \
  --user-data file:///tmp/userdata.sh \
  --query 'Instances[0].InstanceId' --output text)

# 5) Elastic IP — alocar y asociar cuando la instancia esté running
EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC"

EIP=$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" \
  --query 'Addresses[0].PublicIp' --output text)

cat <<INFO
Listo:
  Instance:      $INSTANCE_ID
  EIP:           $EIP
  EIP-con-guiones: ${EIP//./-}
  Security group: $SG_ID
INFO
```

El user-data corre durante ~1–2 min mientras la instancia inicializa. Para verificar que terminó (después de descargar `labsuser.pem` siguiendo el [Paso 2](#paso-2--descargar-la-clave-ssh-labsuserpem)):

```bash
ssh -i /ruta/a/labsuser.pem ubuntu@$EIP \
  'ls /var/log/bootstrap-done && docker version --format "{{.Server.Version}}"'
```

Si ves `/var/log/bootstrap-done` y la versión del server Docker, está listo — saltá directo al [Paso 8](#paso-8--clonar-el-repo-y-configurar-envposter).

> **Por qué user-data y no instalar a mano por SSH:** el grupo `docker` se aplica al usuario `ubuntu` *antes* de que abras tu primera shell SSH, así que no hace falta el `exit` + reconectar del Paso 7 manual. Y el `apt-get install` corre en paralelo a que la instancia siga inicializando — ahorra ~2 min en total.

## Paso 1 — Iniciar el Lab y abrir la consola

1. Entrar a **AWS Academy** → tu curso → **Modules** → **Learner Lab**.
2. Apretar **Start Lab** (botón arriba a la derecha). El círculo al lado pasa de rojo a amarillo a **verde** cuando está listo (~1–2 min).
3. Apretar el círculo verde **AWS** para abrir la consola de AWS en una pestaña nueva.

> Si el círculo está rojo, el lab está apagado. Si está amarillo, está arrancando — esperá.

## Paso 2 — Descargar la clave SSH (`labsuser.pem`)

1. En el panel del Lab, apretar **AWS Details** (arriba a la derecha).
2. Al lado de **SSH key**, apretar **Show** y luego **Download PEM**.
3. Guardar el archivo como `labsuser.pem` en una ubicación conocida.
4. Restringir permisos (en Linux/macOS):
   ```bash
   chmod 400 labsuser.pem
   ```

Esa misma clave se reutiliza entre todas las instancias del Lab, y se puede re-descargar cuantas veces quieras desde **AWS Details**.

## Paso 3 — Crear el security group

> Si ejecutaste la [Vía rápida con AWS CLI](#vía-rápida-pasos-37-desde-aws-cli), saltá directamente al [Paso 8](#paso-8--clonar-el-repo-y-configurar-envposter).

En la consola AWS → **EC2** → **Security Groups** → **Create security group**:

- **Name:** `monitoring-demo-sg`
- **VPC:** la default
- **Inbound rules:**

| Tipo | Puerto | Source | Para qué |
|---|---|---|---|
| SSH | 22 | **My IP** | conectarte desde tu notebook |
| HTTP | 80 | 0.0.0.0/0 | Caddy + Let's Encrypt (HTTP-01 challenge) |
| HTTPS | 443 | 0.0.0.0/0 | tráfico público a Grafana/Prometheus/PanicDuty/Kibana via subdominios sslip.io |
| Custom TCP | 3000 | 0.0.0.0/0 | (opcional, fallback) Grafana directo |
| Custom TCP | 9090 | 0.0.0.0/0 | (opcional, fallback) Prometheus directo |
| Custom TCP | 8080 | 0.0.0.0/0 | (opcional, fallback) PanicDuty directo |
| Custom TCP | 5601 | 0.0.0.0/0 | (opcional, fallback) Kibana directo |

- **Outbound rules:** dejar el default (todo permitido).

Los 4 puertos del fallback (3000/9090/8080/5601) son opcionales pero recomendados si sslip.io tarda en resolver o Caddy no emite cert a tiempo; podés cambiar los QR a `http://<ip>:<puerto>` plano en el momento.

## Paso 4 — Lanzar la instancia EC2

Consola AWS → **EC2** → **Launch instance**:

- **Name:** `monitoring-demo`
- **AMI:** *Ubuntu Server 22.04 LTS (HVM), SSD Volume Type* (la primera que aparece marcada Free tier).
- **Instance type:** `t3.large` (o `t3.medium` si vas a usar modo mínimo).
- **Key pair:** `vockey` (el provisto por el lab).
- **Network settings → Edit:**
  - VPC: default.
  - Subnet: cualquiera de la default VPC.
  - **Auto-assign public IP: Enable.**
  - Firewall: **Select existing security group** → `monitoring-demo-sg`.
- **Configure storage:** 30 GiB, gp3.
- **Advanced details:** dejar todo en default. (No hace falta IAM instance profile para la demo, pero si querés podés ponerle `LabInstanceProfile` — no genera problemas.)
- **Launch instance.**

Esperá a que el estado pase a **Running** y los health checks a **2/2 checks passed** (~1 min). Anotá la **Public IPv4 address** desde el detalle de la instancia.

## Paso 5 — (Recomendado) Asignar una Elastic IP

Sin Elastic IP, cada vez que pares y arranques la instancia (para ahorrar créditos cuando no la usás) **la IP pública cambia**, lo que invalida los hostnames de sslip.io y por lo tanto los QR del póster.

Para evitarlo:

1. **EC2 → Elastic IPs → Allocate Elastic IP address → Allocate.**
2. Seleccionar la EIP recién creada → **Actions → Associate Elastic IP address**.
3. Instance: `monitoring-demo` → **Associate**.

A partir de ahora la IP pública de la instancia es la EIP — sobrevive stop/start. **Si liberás (release) la EIP sin asociarla a nada, AWS te cobra ~$3.60/mes** — al terminar la presentación, primero desasociala y dejala asociada hasta que termines la demo, o liberala junto con la instancia.

> Learner Lab típicamente permite **1 EIP por cuenta**. Si la creación falla por límite, ignorá este paso y asumí que la IP va a cambiar entre stop/start.

## Paso 6 — Conectarse por SSH

Desde tu máquina local:

```bash
ssh -i labsuser.pem ubuntu@<PUBLIC_IP>
```

Si te dice `Permissions 0644 are too open` para `labsuser.pem`, hacé `chmod 400 labsuser.pem` y reintentá.

Si la conexión no responde, lo más probable es que el security group no tenga abierto el 22 desde tu IP — verificá en EC2 → Security Groups → `monitoring-demo-sg`.

## Paso 7 — Instalar Docker, Compose, git y make

Una vez dentro de la instancia (`ubuntu@ip-...`):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git make

# Repo oficial de Docker (incluye docker compose v2 como plugin)
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

**Cerrar sesión y volver a entrar** para que el cambio de grupo `docker` tome efecto:

```bash
exit
ssh -i labsuser.pem ubuntu@<PUBLIC_IP>
```

Verificar:

```bash
docker --version
docker compose version
```

## Paso 8 — Clonar el repo y configurar `.env.poster`

```bash
git clone <URL_DE_TU_REPO> tarea-mlp
cd tarea-mlp/monitoring_demo

cp .env.poster.example .env.poster
nano .env.poster   # o vim
```

Reemplazar `YOUR-IP-WITH-DASHES` por la IP pública de la instancia con puntos cambiados por guiones, en **los 6 hostnames** del template. Ejemplo: si la IP es `54.165.12.30`, queda `54-165-12-30`:

```
GRAFANA_HOST=grafana.54-165-12-30.sslip.io
PROMETHEUS_HOST=prometheus.54-165-12-30.sslip.io
ALERTMANAGER_HOST=alertmanager.54-165-12-30.sslip.io
PANICDUTY_HOST=panicduty.54-165-12-30.sslip.io
API_HOST=api.54-165-12-30.sslip.io
KIBANA_HOST=kibana.54-165-12-30.sslip.io

GF_ANONYMOUS_ROLE=Viewer
GF_ADMIN_PASSWORD=<una-clave-fuerte>
```

> **Importante:** cambiá `GF_ADMIN_PASSWORD` por algo fuerte — Grafana queda accesible desde internet.

## Paso 9 — Levantar el stack

```bash
make poster-fresh
```

Equivalente a `docker compose --env-file .env.poster --profile poster down --rmi local --remove-orphans` seguido de `... up -d --build`. Es el target recomendado para arrancar la demo: rebuildea las imágenes locales (`model_api`, `panic_duty`) y recrea todos los contenedores con estado fresco (Elasticsearch/Kibana/Prometheus quedan vacíos — no tienen volúmenes nombrados), pero **preserva el volumen nombrado `caddy_data`** así que los certs Let's Encrypt sobreviven al rebuild. El primer `poster-fresh` después de desplegar a una IP nueva tarda ~3–5 min (baja imágenes pesadas + emite certs LE por primera vez); los siguientes son ~30–60 s y reusan los certs cacheados.

Para iteración rápida cuando solo cambia código (sin rebuild de imágenes) podés usar `make poster-up` — es más liviano. Pero `poster-fresh` ya no tiene riesgo de agotar el rate limit de Let's Encrypt, así que correrlo varias veces seguidas es seguro.

Mirá el progreso con:

```bash
make poster-logs       # Ctrl+C para salir, no detiene los contenedores
make poster-status     # tabla de estado de los 11 servicios
```

Esperá a que `kibana-init` aparezca como `Exit 0` y el resto como `Up` o `Up (healthy)`.

## Paso 10 — Verificar las 6 URLs públicas

> **Nota:** las URLs de abajo usan `3-226-31-220` porque es la IP del despliegue real del póster en el momento de escribir esta guía. **Reemplazá `3-226-31-220` por tu EIP en formato con guiones** (la misma que pusiste en `.env.poster` en el Paso 8).

Desde tu navegador (idealmente desde el celular en datos móviles, para confirmar que es alcanzable de afuera):

- [https://grafana.3-226-31-220.sslip.io](https://grafana.3-226-31-220.sslip.io) → dashboard `ML System Dashboard`.
- [https://prometheus.3-226-31-220.sslip.io](https://prometheus.3-226-31-220.sslip.io) → UI de Prometheus.
- [https://alertmanager.3-226-31-220.sslip.io](https://alertmanager.3-226-31-220.sslip.io) → UI de Alertmanager.
- [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io) → UI de alertas (PanicDuty).
- [https://api.3-226-31-220.sslip.io/docs](https://api.3-226-31-220.sslip.io/docs) → Swagger UI del Model API.
- [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover) → logs (Kibana Discover).

El primer hit a cada subdominio tarda ~30 s mientras Caddy negocia el certificado de Let's Encrypt. Después es instantáneo.

> Si alguna URL no resuelve después de 1 min, mirá [Troubleshooting](#troubleshooting).

### URLs útiles del stack en este deployment

Mapa completo equivalente a la sección [sección 3 *Acceso a cada servicio*](descripcion_demo_es.md#3-acceso-a-cada-servicio) de `descripcion_demo_es.md`. Todas las herramientas pasan por Caddy (HTTPS con cert de Let's Encrypt) en este deployment. La IP en formato con guiones es `3-226-31-220`.

| Servicio | URL pública |
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

Las 6 herramientas viven detrás de subdominios `*.sslip.io` proxeados por Caddy a los servicios del stack (`grafana:3000`, `prometheus:9090`, `alertmanager:9093`, `panic_duty:8080`, `model_api:8000`, `kibana:5601`). Caddy emite cert Let's Encrypt en el primer hit a cada subdominio (~30 s la primera vez, instantáneo después).

## Operación día a día

### Cuando termines de usarla — **detener** (stop, no terminar)

Desde la consola AWS → EC2 → seleccionar la instancia → **Instance state → Stop instance**. Detener pausa el cobro de cómputo (lo más caro) pero conserva el disco, la configuración, las imágenes Docker construidas y la EIP asociada. **Crítico para no consumir los créditos del Learner Lab.**

Cuando vuelvas, **Start instance** y en ~1 min está disponible de nuevo. Te conectás por SSH, `cd tarea-mlp/monitoring_demo`, `make poster-up` (preserva volúmenes — los certs Let's Encrypt se mantienen), y eso es todo. Si querés un arranque limpio borrando datos previos, usá `make poster-fresh` en lugar de `poster-up`.

### Cuando vence la sesión del Lab (cada 4 h)

El círculo del Learner Lab vuelve a rojo. La instancia EC2 sigue en el estado en que la dejaste — si la dejaste corriendo, sigue corriendo y consumiendo créditos. Si querés volver a abrir la consola AWS, apretás **Start Lab** otra vez. La sesión es para *tu acceso a la consola AWS*, no para *los recursos*.

### Si la IP pública cambió (no usaste EIP, o cambiaste la EIP)

```bash
nano .env.poster        # actualizar los 6 hostnames con la IP nueva
make poster-fresh       # elimina y recrea todo, fuerza la re-emisión de certs con los hostnames nuevos
```

Caddy va a re-emitir certs nuevos (~30 s por subdominio en el primer hit).

## Troubleshooting

### `https://...sslip.io` no carga / da `ERR_CONNECTION_REFUSED`

- Verificá que el security group tenga 80 y 443 abiertos a `0.0.0.0/0`.
- Verificá que Caddy esté arriba: `make poster-status` → debería ver `caddy` como `Up`.
- Verificá los logs: `docker logs caddy --tail 100`. Si ves errores tipo `unable to authorize` o `connection refused`, casi siempre es el puerto 80 cerrado en el security group.

### Caddy emite cert pero el navegador dice `NET::ERR_CERT_AUTHORITY_INVALID`

Le emitió un cert de **staging** de Let's Encrypt porque alcanzaste el rate limit de producción. Esperá ~1 h a que el rate limit se libere y reintentá (en Learner Lab cambiar de IP no es trivial — solo tenés 1 EIP). Eso pasa típicamente cuando reiniciás Caddy 5+ veces en pocos minutos.

### `kibana-init` queda como `Exit 1` (falló al importar el dashboard)

Es no-fatal — la demo igual funciona sin el dashboard auto-provisionado `ML Drift Investigation` de Kibana (2 paneles Lens). Para reintentar:

```bash
docker compose --env-file .env.poster --profile poster up -d --force-recreate kibana-init
```

### Memoria insuficiente / Elasticsearch reiniciándose en loop

- Estás en `t3.medium` (4 GB) → cambiá a `t3.large` (parar instancia → Actions → Instance settings → Change instance type → `t3.large` → start).
- O bajá al [modo mínimo de 3 servicios](../README_ES.md#mínimo-local-poca-ram) (sin ELK, sin alertas) y prescindí de Caddy/poster.

### `make: command not found`

`sudo apt-get install -y make`. (En Ubuntu 22.04 server algunas AMIs no lo traen).

### `make poster-fresh` (o `make poster-up`) falla con `permission denied while trying to connect to the Docker daemon socket`

No cerraste sesión SSH después de `usermod -aG docker ubuntu`. Hacé `exit` y volvé a entrar.

### Las URLs van pero los QR del póster no — cambié a otra IP

Regenerá los QR con la nueva IP, **o** preferiblemente codificá los QR contra un acortador (bit.ly) y cambiá el destino del redirect — ver [Tips para los QR](../README_ES.md#tips-para-los-qr) en el README.

## Desmontar todo al terminar

Cuando terminó el póster y ya no necesitás la demo:

1. **Bajar el stack:**
   ```bash
   make poster-down
   ```
2. **Desde la consola AWS:**
   - EC2 → Instances → seleccionar `monitoring-demo` → **Instance state → Terminate instance** (esto sí borra el disco — irreversible).
   - EC2 → Elastic IPs → seleccionar la EIP → **Actions → Release Elastic IP address**. (Si dejás la EIP sin asociar, AWS cobra ~$3.60/mes.)
   - EC2 → Security Groups → `monitoring-demo-sg` → **Delete** (opcional, no tiene costo).
3. **End Lab** en el panel de Learner Lab para cerrar la sesión limpiamente.

