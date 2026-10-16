# orders

Second sample service on the AWS platform (EKS), next to `my-app`. A small **Python** service that serves sample
orders and **calls my-app over the cluster network**, so the platform runs more than one microservice and they
talk to each other. Deployed by `AjinOrg/helm-chart` (shared chart `charts/microservice`, values in `apps/orders/`).
URL: `http://<alb>/<environment>/orders`.

## How it differs from my-app

| | my-app | orders |
|---|---|---|
| Language / server | Node.js 22, `node:http` | Python 3.13, waitress (pure-Python WSGI server) |
| Runtime dependencies | none | one (`waitress`), exact version + hash (`requirements.txt`) |
| Talks to other services | no | yes: calls my-app at `MY_APP_URL` (`http://my-app`, same namespace) |
| Secrets / AWS access | yes: `GREETING` secret, IRSA role | **none**: no ExternalSecret, no IAM role (`secrets.enabled: false`) |
| Network policy | ALB → my-app | ALB → orders, **orders → my-app** (only this pair is opened) |
| Lint / tests | ESLint, `node --test` | ruff (incl. security rules), pytest + coverage |
| Dependency scan | npm audit + OWASP Dependency-Check | pip-audit (PyPI advisories + OSV) |
| Extra pipeline steps | | **container test** (read-only, non-root, SIGTERM) and **SBOM** (CycloneDX) |
| ECR push role | `aws-platform-ecr-push` | `orders-ecr-push` (own role: it can push only to `orders`) |

## Endpoints

| Path | Answer |
|---|---|
| `/healthz`, `/readyz` | liveness / readiness. They do **not** depend on my-app: if my-app is down, orders stays up and says so |
| `.../api/orders` | the three sample orders |
| `.../api/orders/<id>` | one order, or 404 |
| anything else (e.g. `/dev/orders`) | service info: environment, version, pod, and `dependencies.my-app` (reachable? which version?) |

Example, in dev:
```bash
ALB=$(kubectl get ingress -n dev -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}')
curl -s http://$ALB/dev/orders | jq .                 # dependencies.my-app.reachable: true, version: <my-app tag>
curl -s http://$ALB/dev/orders/api/orders/1001 | jq .
```

## Configuration

| Variable | Default | Set by |
|---|---|---|
| `PORT` | `8080` | Helm chart |
| `APP_ENV` | `local` | Helm chart (`dev`, `staging`, `production`) |
| `APP_VERSION` | `dev` | Helm chart (the image tag) |
| `MY_APP_URL` | empty (= not configured) | helm-chart `apps/orders/values.yaml` (`extraEnv`) |

## Develop
Python 3.13 or newer:
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest --cov --cov-report=term-missing
pip-audit --require-hashes --disable-pip -r requirements.txt
PORT=8080 MY_APP_URL=http://localhost:8081 python -m orders.server    # http://localhost:8080/dev/orders
```
Upgrade waitress: `pip download --no-deps waitress==<new>`, `pip hash waitress-<new>-py3-none-any.whl`, put the
version and hash in `requirements.txt`.

## Container image
```bash
docker build -t orders:local .
docker run --rm -p 8080:8080 --read-only --user 65532 --cap-drop ALL orders:local
```
- Multi-stage build; runtime image is **distroless** `python3-debian13` (no shell, no pip, no package manager).
- Dependencies installed as wheels only, with hashes checked, into `/app/deps` (`PYTHONPATH`).
- Runs as **non-root** (uid 65532), works with a **read-only** root filesystem (`PYTHONDONTWRITEBYTECODE`).
- Exits cleanly on `SIGTERM` (finishes open requests); the pipeline tests this on every build.

## How a change reaches the cluster
Same as my-app:
```
PR to main    → Gitleaks, ruff, pytest + coverage, SonarQube Cloud, pip-audit, Trivy fs,
                image build + Trivy image + SBOM + container test + Kyverno   → must be green to merge
merge to main → image build + Trivy + container test + Kyverno → ECR (tag = 7-char commit SHA, OIDC role orders-ecr-push)
              → dev:        PR in helm-chart merges itself after its ci, then smoke-tested (automatic)
              → staging / production: released on purpose with helm-chart's release.yaml (scripts/release.sh)
```

## First deployment (one time)
The pipeline's Kyverno step and the dev PR need `apps/orders/` in helm-chart, but those values need a real image
tag. So the first image is built while orders is not yet registered: the pipeline skips Kyverno (with a warning)
and the dev PR, and its summary prints the tag to register. helm-chart's ci then runs the same Kyverno policies on
the PR that registers orders. The full order of steps (infrastructure first) is in the aws-platform README,
"Step 17: A Second Microservice (orders)".
