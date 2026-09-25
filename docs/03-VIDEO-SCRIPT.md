# Video script

Shot by shot. Say the bracketed line in your own words, don't read it verbatim.
Everything here is something the running system actually does - if a step
doesn't work on your machine, it's a setup problem (see `docs/02-RUN-GUIDE.md`),
not a script to fake.

**Total length: aim for 8-12 minutes.** Examiners watch a lot of these; a
demo that is confident and quick beats one that is thorough and slow.

Before you start recording: run `scripts/2-deploy.ps1` so the cluster is up,
the ten services are healthy and a demo day of meter data is loaded.

---

## 1. Thirty seconds on the idea (talking head or slides)

> "MediMatrx moves a hospital's deferrable electricity use - laundry,
> sterilisation, catering, HVAC pre-cooling - into the cheapest hours of the
> day, without ever touching clinical load. It's a Kubernetes microservice
> application: ten application microservices plus MongoDB, in two languages,
> deployed as 61 Kubernetes resources across 15 manifest files."

---

## 2. The architecture, on screen for 20 seconds

Show `docs/architecture-diagram.svg` (open it in a browser) or the ASCII
diagram in `README.md`. Both use the same C1-C11 component numbers as
`docs/01-REPORT.md`, so point at the diagram while you say the tiers:

> "C1, the gateway, is the only externally reachable service - NodePort
> 30080. Tier 1 owns something: C2 ingest owns the meter data, C3 price
> caches the market, C4 optimizer owns the algorithm, C7 auth owns the
> identity data. Tier 2 only reads: C5 assistant and C6 forecast build on
> Tier 1, and C8 verification, C9 validation and C10 authorization each
> answer one narrow identity question. C11 MongoDB is one shared instance -
> C2 and C7 are the only two services that connect to it, each restricted to
> its own collections."

---

## 3. What's actually running

```powershell
kubectl get pods -n medimatrx
```

> "Ten services at two replicas each, plus one MongoDB pod - 21 pods, all
> Running."

```powershell
kubectl get hpa -n medimatrx
```

> "Every service has its own HorizontalPodAutoscaler - ten of them - each with
> its own target and ceiling. Nothing is shared between them."

```powershell
kubectl get pvc -n medimatrx
```

> "And MongoDB's data lives on a PersistentVolumeClaim, not inside the pod."

---

## 4. The dashboard, through the NodePort

Open **http://localhost:30080** in a browser (already open before you start
recording saves dead air).

> "This is the only externally reachable address in the whole system - the
> gateway's NodePort Service, on port 30080. Everything else is ClusterIP,
> unreachable from outside the cluster."

Point at the numbers on screen: the savings figure, the peak-reduction figure,
the chart with the orange/blue bars, the green price line.

---

## 5. Registration, login, roles

Open the login page and register a new account with role **viewer**.

> "Roles are `staff`, who can read and write, and `viewer`, read-only. Chosen
> at sign-up for this demo - a real deployment would have an administrator
> assign it instead, and that's written up as a known limitation."

The response includes a verification code (there is no mail server in this
demo). Paste it into the verify step, then log in.

```powershell
curl.exe -X POST http://localhost:30080/api/readings -H "Authorization: Bearer <viewer-token>" -H "Content-Type: application/json" -d "{\"zoneId\":\"laundry\",\"kwh\":50}"
```

> "That's a 403 - a viewer account can't write. The check happens in the
> authorization service, server-side, not by hiding a button in the browser."

Now register and log in as **staff**, and repeat the same call.

> "Same call, staff token, 201 - the reading is stored. That's the
> authorization flow: the gateway asks the authorization service before it
> proxies a single write anywhere."

---

## 6. Validation, on a bad request

```powershell
curl.exe -X POST http://localhost:30080/api/readings -H "Authorization: Bearer <staff-token>" -H "Content-Type: application/json" -d "{\"zoneId\":\"not-a-real-zone\",\"kwh\":-5}"
```

> "400, with a list of errors: unknown zone, and a negative kWh. The gateway
> called the validation service before it ever reached ingest - bad data never
> lands in the database."

---

## 7. The optimizer

```powershell
curl.exe http://localhost:30080/api/optimize?area=SE4
```

Or just point at the dashboard's numbers again.

> "The optimizer fetches today's consumption from ingest and today's prices
> from price, concurrently, computes a load-shifting plan, and reports it. It
> never touches a clinical zone - that rule is enforced here, not in the UI."

---

## 8. Logs

```powershell
kubectl logs -l app=optimizer -n medimatrx --tail=20
kubectl logs -l app=price -n medimatrx --tail=10
```

> "One structured JSON line per request, tagged with service and pod name.
> The price service log shows it actually calling elprisetjustnu.se - that's
> the live public API, not a mock."

---

## 9. The Kubernetes YAML

Open `k8s/08-network-policy.yaml` and `k8s/07-autoscaling.yaml` briefly.

> "Default-deny, then 15 explicit allows - only the pairs of services that
> actually need to talk. And ten independent autoscalers, one per service.
> Worth saying honestly: Docker Desktop's default network plugin doesn't
> enforce NetworkPolicy, so these objects are correct but inert here - they'd
> be enforced on a cluster running Calico or Cilium."

---

## 10. Independent horizontal scaling

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\3-demo-scaling.ps1
```

Let it run; narrate over the pauses.

> "Scaling only the optimizer, from 2 to 6 replicas - the other nine
> deployments don't move. Then 20 requests, spread across the new pods with no
> code change. Then a pod gets deleted outright and Kubernetes replaces it
> without being asked."

---

## 11. Persistent storage

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\4-demo-persistence.ps1
```

> "The whole MongoDB pod gets deleted on camera. Kubernetes recreates it,
> reattaches the same PersistentVolumeClaim, and the data is still there -
> that's what 'persistent across restarts of the deployment infrastructure'
> means in practice."

---

## 12. Close

> "Ten microservices in two languages, MongoDB on persistent storage, 61
> Kubernetes objects, independent scaling, and a real safety rule enforced
> server-side. Full write-up, including what's deliberately not done yet and
> how I'd fix it, is in the report."

---

## What NOT to say

- Don't call this "production-ready" - it isn't, and the report says so.
- Don't claim the NetworkPolicies are protecting anything on Docker Desktop -
  say plainly that they aren't enforced there.
- Don't claim clinical deployment or real hospital use. This is a prototype
  on simulated meter data with live prices and live weather.
