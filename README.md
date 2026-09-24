# MediMatrix — Diabetes Risk Predictor (Cloud-Native Microservices)

A cloud-native, microservices-based application that predicts diabetes risk from
patient health data. The system is built for Docker Compose (local development)
and Kubernetes (production-style deployment), and is made up of seven
independently deployable services.

## Architecture

```
                    +-------------+
                    |  Frontend   |
                    |  (Flask UI) |
                    +------+------+
                           |
      +--------------------+--------------------+
      |                    |                     |
      v                    v                     v
+-------------+    +----------------+    +----------------------+
| auth-service|    | verification-  |    |       backend         |
| (login/     |<-->|   service      |    |   (ML prediction)     |
|  register)  |    | (OTP codes)    |    |                        |
+------+------+    +--------+-------+    +----+--------------+---+
       |                    |                 |              |
       |                    |                 v              v
       |                    |        +----------------+  +----------------+
       |                    |        | authorization- |  | validation-    |
       |                    |        |   service      |  |   service      |
       |                    |        +----------------+  +----------------+
       |                    |
       +---------+----------+
                 v
          +-------------+
          |  PostgreSQL |
          |  (db)       |
          +-------------+
```

### Services

| Service | Port | Responsibility |
|---|---|---|
| `frontend` | 5000 | Web UI (login, register, verify, prediction form) |
| `backend` | 5001 | Loads the ML model, runs predictions, calls authorization + validation before every prediction |
| `auth-service` | 5002 | **Authentication** — registration, login, JWT issuance |
| `verification-service` | 5003 | **Verification** — generates and confirms one-time account verification codes |
| `validation-service` | 5004 | **Validation** — checks prediction input is well-formed and clinically plausible |
| `authorization-service` | 5005 | **Authorization** — decodes the JWT and enforces role-based permissions |
| `db` | 5432 | PostgreSQL — stores `users`, `verification_codes`, and `predictions` |

### Request flow

1. A user **registers** via the frontend → `auth-service` creates the account (unverified)
   and asks `verification-service` to generate a one-time code.
2. The user **verifies** the code → `verification-service` marks the account verified.
3. The user **logs in** → `auth-service` checks the password and issues a JWT.
4. The user submits the **prediction form** → the frontend sends the JWT + features to
   `backend`.
5. `backend` asks `authorization-service` "is this token allowed to `predict`?" and
   `validation-service` "is this feature vector valid?" before running the model and
   writing the result to Postgres.

Any failure at steps 4–5 (missing/expired token, wrong role, malformed input) is
rejected before the ML model ever runs.

## Repository structure

```
/frontend/                  Flask web UI (login, register, verify, predictor)
/backend/                   Prediction REST API + ML model (diabetes_model.pkl)
/auth-service/              Authentication microservice
/verification-service/      Verification microservice
/validation-service/        Validation microservice
/authorization-service/     Authorization microservice
/db/init.sql                Postgres schema (users, verification_codes, predictions)
/k8s/                        Kubernetes manifests (Deployments, Services, ConfigMap, Secret, PVC)
/docker-compose.yaml         Local multi-container orchestration
```

## Running locally with Docker Compose

Requires Docker Desktop (or Docker Engine + Compose plugin).

```bash
docker compose build
docker compose up -d
```

This builds and starts all seven services, creates the `db-data` named volume for
PostgreSQL, and runs `db/init.sql` automatically on first boot to create the schema.

- Frontend: http://localhost:5000
- Backend API: http://localhost:5001
- auth-service: http://localhost:5002
- verification-service: http://localhost:5003
- validation-service: http://localhost:5004
- authorization-service: http://localhost:5005
- Postgres: localhost:5432 (`postgres` / `password`, db `microservices_db`)

Check everything is healthy:

```bash
docker compose ps
docker compose logs -f
```

Tear down (and delete the database volume):

```bash
docker compose down -v
```

### Try it end-to-end (curl)

```bash
# 1. Register
curl -X POST http://localhost:5002/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"secret123"}'
# -> {"user_id": 1, "verification_code": "123456", ...}

# 2. Verify (code comes from the register response — no mail server in this demo)
curl -X POST http://localhost:5003/confirm \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "code": "123456"}'

# 3. Log in
curl -X POST http://localhost:5002/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret123"}'
# -> {"token": "<jwt>", "username": "alice", "role": "user"}

# 4. Predict (token required)
curl -X POST http://localhost:5001/predict \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt>" \
  -d '{"features":[2,120,70,20,80,25.5,0.5,30]}'
# -> {"prediction": "Non-Diabetic"}
```

Or just open http://localhost:5000 and use the Register → Verify → Log In → Predict
flow in the browser.

## Running on Kubernetes

Requires a running cluster (e.g. Docker Desktop's built-in Kubernetes, or minikube)
and `kubectl` configured against it.

### 1. Build images

The manifests reference `abhinavannareddy/<service>` images. Build them locally
(Kubernetes will use the local image if it's already present and
`imagePullPolicy: IfNotPresent` is set, which every manifest here uses):

```bash
docker build -t abhinavannareddy/back ./backend
docker build -t abhinavannareddy/front ./frontend
docker build -t abhinavannareddy/auth-service ./auth-service
docker build -t abhinavannareddy/verification-service ./verification-service
docker build -t abhinavannareddy/validation-service ./validation-service
docker build -t abhinavannareddy/authorization-service ./authorization-service
```

To deploy on a remote cluster instead of a local one, push these images to a
registry and update the `image:` field in each `k8s/*-deployment.yaml` accordingly.

### 2. Apply the manifests

```bash
kubectl apply -f k8s/
```

This creates:
- `app-config` (ConfigMap) — non-secret settings (DB host/name/user, internal service URLs)
- `app-secret` (Secret) — `DB_PASSWORD`, `JWT_SECRET`, frontend `SECRET_KEY`
- `db-init-sql` (ConfigMap) — the schema, mounted into the Postgres pod's
  `/docker-entrypoint-initdb.d` so tables are created automatically on first boot
- A Deployment + Service for each of: `db`, `backend`, `frontend`, `auth-service`,
  `verification-service`, `validation-service`, `authorization-service`
- `db-pvc` (PersistentVolumeClaim) — durable storage for PostgreSQL

### 3. Check status

```bash
kubectl get pods
kubectl get svc
```

All pods should reach `1/1 Running`. Every service has liveness/readiness probes
(HTTP `/health` for the app services, `pg_isready` for Postgres).

### 4. Reach the app

`frontend` is a `LoadBalancer` Service. On Docker Desktop's Kubernetes this is
reachable directly at http://localhost:5000. On minikube, run:

```bash
minikube service frontend --url
```

For any service, you can also port-forward directly, e.g.:

```bash
kubectl port-forward svc/backend 5001:5001
kubectl port-forward svc/auth-service 5002:5002
```

### Teardown

```bash
kubectl delete -f k8s/
```

## Configuration

All configuration is environment-variable driven — nothing is hardcoded in the
images. In Docker Compose these are set directly in `docker-compose.yaml`; in
Kubernetes they come from the `app-config` ConfigMap and `app-secret` Secret.

| Variable | Used by | Purpose |
|---|---|---|
| `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | backend, auth-service, verification-service, db | PostgreSQL connection |
| `JWT_SECRET` | auth-service, authorization-service | Shared secret for signing/verifying JWTs |
| `JWT_EXPIRY_MINUTES` | auth-service | Token lifetime (default 60) |
| `SECRET_KEY` | frontend | Flask session signing key |
| `AUTH_SERVICE_URL`, `VERIFICATION_SERVICE_URL`, `VALIDATION_SERVICE_URL`, `AUTHORIZATION_SERVICE_URL`, `BACKEND_URL` | frontend, backend, auth-service | Internal service discovery (Compose service names / Kubernetes Service names) |

For a real deployment, replace the demo values of `JWT_SECRET`, `SECRET_KEY` and
`DB_PASSWORD` in `k8s/app-secret.yaml` with your own secrets before applying.

## API reference

### auth-service (authentication) — port 5002

| Method & Path | Body | Response |
|---|---|---|
| `POST /register` | `{username, email, password}` | `201` `{user_id, verification_code}` — creates an unverified user and triggers `verification-service` |
| `POST /login` | `{username, password}` | `200` `{token, username, role}` — fails with `403` if the account isn't verified yet |
| `GET /health` | — | `{status: "ok"}` |

### verification-service (verification) — port 5003

| Method & Path | Body | Response |
|---|---|---|
| `POST /generate` | `{user_id}` | `201` `{code, expires_at}` — issues a 6-digit code, valid 15 minutes |
| `POST /confirm` | `{user_id, code}` | `200` `{verified: true}` — marks the user verified, or `400` on an invalid/expired code |
| `GET /health` | — | `{status: "ok"}` |

### validation-service (validation) — port 5004

| Method & Path | Body | Response |
|---|---|---|
| `POST /validate` | `{features: [pregnancies, glucose, blood_pressure, skin_thickness, insulin, bmi, dpf, age]}` | `200` `{valid: true}` or `400` `{valid: false, errors: [...]}` — checks each field is numeric and within a clinically plausible range |
| `GET /health` | — | `{status: "ok"}` |

### authorization-service (authorization) — port 5005

| Method & Path | Body | Response |
|---|---|---|
| `POST /authorize` | `{token, action}` | `200` `{authorized: true, username, role}`, or `401`/`403` with an error if the token is invalid/expired or the role lacks permission |
| `GET /health` | — | `{status: "ok"}` |

### backend (prediction API) — port 5001

| Method & Path | Headers / Body | Response |
|---|---|---|
| `POST /predict` | `Authorization: Bearer <jwt>`, body `{features: [...]}` | `200` `{prediction: "Diabetic" \| "Non-Diabetic"}` after passing authorization + validation checks; stores the result in `predictions` |
| `GET /health` | — | `{status: "ok"}` |

## Security notes

- Passwords are hashed with Werkzeug's `generate_password_hash` (PBKDF2) — never
  stored in plaintext.
- JWTs are signed with `HS256` using a secret shared only between `auth-service`
  (which issues them) and `authorization-service` (which verifies them); no other
  service can mint a valid token.
- Accounts must be verified before they can log in.
- Secrets (`DB_PASSWORD`, `JWT_SECRET`, `SECRET_KEY`) live in a Kubernetes `Secret`,
  not in source code or the ConfigMap.
- `validation-service` rejects malformed/out-of-range input before it ever reaches
  the ML model or the database, mitigating injection/garbage-input attacks.
