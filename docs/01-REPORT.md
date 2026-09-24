# MediMatrx: Hospital Energy Optimisation Platform

**Cloud Computing assignment report**
Blekinge Institute of Technology

---

## 1. What the software does

MediMatrx helps a hospital spend less on electricity without touching any of the
equipment that keeps patients alive.

Hospitals are awkward electricity customers. They run around the clock, they
cannot switch off, and a large Swedish regional hospital spends somewhere around
8-15 MSEK a year on power. But the load is not all of one kind:

- **Clinical load**: intensive care, operating theatres, imaging, wards. This is
  life-safety equipment. It runs when a patient needs it and at no other time.
  MediMatrx never touches it.
- **Deferrable load**: the laundry, sterilisation, the kitchen's bulk cooking and
  dishwashing, and the ventilation plant, which can pre-cool the building and let
  its thermal mass hold the result. The work still happens every day. Only the
  timing is open.

Swedish electricity is traded a day ahead and repriced every hour. On an ordinary
day in bidding area SE4 the cheapest hour costs about a third of the dearest one.
A hospital running its laundry at 18:00 because it has always run its laundry at
18:00 is paying roughly three times what it needs to.

The system does six things:

1. **Collects** meter readings from nine metered zones and stores them.
2. **Fetches** today's real hourly prices from the public Swedish spot-price API
   at `elprisetjustnu.se`.
3. **Computes** a plan that moves deferrable work out of expensive hours into
   cheap ones, with clinical zones excluded, and reports the money, the peak
   reduction and the carbon involved.
4. **Watches** for equipment faults. An hour where a zone drew far more power
   than the hours either side usually means a chiller is short-cycling or an
   air-handling unit has a stuck damper. Faults like these waste money for weeks
   before anything actually breaks.
5. **Explains** itself. An estates manager can type a question in English and get
   an answer built from the same numbers the dashboard shows, with the services
   consulted listed underneath.
6. **Plans tomorrow.** It pulls the weather forecast, predicts what the building
   will use, fetches tomorrow's day-ahead prices, and produces a plan for a day
   that has not happened yet.

The first five describe the past. Only the sixth gives an estates manager
something to act on when they arrive in the morning.

Results are shown on a web dashboard that could sit on a wall screen.

### A note on the demo data

The meter readings are **simulated**, generated from realistic 24-hour load
profiles for each type of department, with random noise and two deliberately
injected equipment faults. In a real deployment `POST /api/readings` would be
called by the hospital's building management system or by IoT meter gateways.
Nothing else about the system changes.

The electricity prices and the weather forecast are real and live, from
`elprisetjustnu.se` and Open-Meteo, both public and neither needing a key. The
meter readings are the only simulated input, and they are the one input a real
customer would already have.

### Scale, honestly

One hospital does not need any of this to scale. A single pod of each service
would carry the load without trouble, and I would rather say so than pretend
otherwise. The scaling argument only becomes real if MediMatrx is run as a
product for a group of hospitals:

- Region Blekinge alone has several hospitals and health centres.
- A national or Nordic operator would carry hundreds of sites, each with dozens
  of metered zones reporting every few minutes.
- At that point the workloads pull apart. Ingest grows with the number of meters,
  the optimizer grows with the number of sites being replanned, and the price
  service does not grow at all, because prices are the same for everyone in a
  bidding area.

Three different growth curves is the reason these are three different services.

*For the demonstration to mean anything, read the nine zones as nine hundred
zones across sixty hospitals, with the optimiser replanning every site every
fifteen minutes as new price data arrives.*

---

## 2. Software architecture design

### 2.1 The picture

The system is built in two tiers. Tier 1 services each own something: the meter
data, the price cache, or the optimisation algorithm. Tier 2 services own
nothing. They answer questions and build plans by calling Tier 1, so there is one
copy of the algorithm and one copy of the data in the whole system.

```
                  ┌──────────────────────────────┐
                  │   Hospital estates manager   │
                  │        (a web browser)       │
                  └───────────────┬──────────────┘
                                  │ HTTP :30080
   ═══════════════════════════════╪═══════════════════════════════
     KUBERNETES CLUSTER           │  NodePort - the only way in
   ═══════════════════════════════╪═══════════════════════════════
                                  ▼
                  ┌──────────────────────────────┐
                  │        API GATEWAY           │  Node.js / Express
                  │  serves the dashboard and    │  2-10 replicas
                  │  routes every API call       │
                  └───────────────┬──────────────┘
                                  │
   ── TIER 1: services that own something ──────────────────────────
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
 ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
 │    INGEST    │        │    PRICE     │        │  OPTIMIZER   │
 │ Node/Express │        │ Py / FastAPI │        │ Py / FastAPI │
 │  2-12 pods   │        │   2-4 pods   │        │  2-15 pods   │
 │              │        │              │        │              │
 │ owns all     │        │ caches SE1   │        │ owns the     │
 │ meter data   │        │ to SE4       │        │ algorithm,   │
 │              │        │ prices       │        │ stateless    │
 └──────┬───────┘        └──────┬───────┘        └──────────────┘
        │                       │                        ▲
        │ mongodb :27017        │ HTTPS                  │
        ▼                       ▼                        │
 ┌──────────────┐        ┌──────────────┐                │
 │   MONGODB    │        │ elprisetjust │                │
 │  StatefulSet │        │ nu.se        │                │
 │  1 replica   │        │ (day-ahead   │                │
 │  ┌────────┐  │        │  prices)     │                │
 │  │  PVC   │  │        └──────────────┘                │
 │  │ 2 GiB  │  │                                        │
 │  └────────┘  │                                        │
 └──────────────┘                                        │
                                                         │
   ── TIER 2: services that only read ────────────────────┼───────
                                                         │
        ┌────────────────────────────────────────────────┤
        │                                                │
 ┌──────┴───────┐                                 ┌──────┴───────┐
 │  ASSISTANT   │                                 │   FORECAST   │
 │ Py / FastAPI │                                 │ Py / FastAPI │
 │   2-6 pods   │                                 │   2-4 pods   │
 │              │                                 │              │
 │ answers      │                                 │ predicts     │
 │ questions in │                                 │ tomorrow,    │
 │ plain words  │                                 │ then asks    │
 │              │                                 │ the optimizer│
 └──────────────┘                                 └──────┬───────┘
                                                         │ HTTPS
                                                         ▼
                                                  ┌──────────────┐
                                                  │  Open-Meteo  │
                                                  │  (weather    │
                                                  │   forecast)  │
                                                  └──────────────┘
```
### 2.2 Mapping software components to microservices

The assignment asks for an explicit mapping between the logical components and
the microservices implementing them. Here it is.

| # | Logical component | Implemented by | Language / framework | Owns state? | Docker Hub image |
|---|---|---|---|---|---|
| C1 | Presentation & entry point | **API Gateway** (`gateway`) | Node.js 20 / Express | No | `medimatrx-gateway:1.4.0` |
| C2 | Metering data management | **Ingest Service** (`ingest`) | Node.js 20 / Express | **Yes, owns MongoDB** | `medimatrx-ingest:1.4.0` |
| C3 | Market price acquisition | **Price Service** (`price`) | Python 3.12 / FastAPI | In-memory cache only | `medimatrx-price:1.4.0` |
| C4 | Optimisation & analytics | **Optimizer Service** (`optimizer`) | Python 3.12 / FastAPI | No, fully stateless | `medimatrx-optimizer:1.4.0` |
| C5 | Natural-language explanation | **Assistant Service** (`assistant`) | Python 3.12 / FastAPI | No, fully stateless | `medimatrx-assistant:1.4.0` |
| C6 | Next-day prediction & planning | **Forecast Service** (`forecast`) | Python 3.12 / FastAPI | No, fully stateless | `medimatrx-forecast:1.4.0` |
| C7 | Persistence | **MongoDB** | MongoDB 7.0 | Yes | `mongo:7.0` (official) |
Six application microservices plus a database. C1 to C4 are Tier 1 in the diagram
above, C5 and C6 are Tier 2. What the two Tier 2 services share is that neither
holds any business logic; both call C4 for every number they report.

#### C1: API Gateway

*Responsibility:* the single front door. Serve the dashboard, and forward each
API call to whichever internal service owns that job. Apply the cross-cutting
work once: security headers, rate limiting, request logging, upstream timeouts.

*Why it exists:* without it the browser would need six addresses and all six
services would need exposing. With it, one pod has a public door and the other
five are unreachable from outside the cluster.

#### C2: Ingest Service

*Responsibility:* receive meter readings, validate them, store them, serve them
back. It exposes `POST /api/readings` (what a smart meter calls),
`GET /api/readings`, and `GET /api/summary`, which aggregates the last 24 hours
into a per-zone, per-hour matrix through a MongoDB aggregation pipeline.

*Why it exists separately:* it is the only component with a database, and its
load grows with the number of meters. Writing is a different workload from
computing, and putting both in one service would mean scaling both whenever
either came under pressure.

#### C3: Price Service

*Responsibility:* a client of somebody else's REST API and a server of its own.
It calls `elprisetjustnu.se`, normalises whatever comes back into exactly 24
hourly values, caches for 15 minutes, and serves it.

*Why it exists separately:* it is one of only two components that touch the
public internet, the other being the forecast service. Those two have the largest
attack surface and are the most likely to fail for reasons outside our control.
Keeping them separate means an outage at `elprisetjustnu.se` cannot stop meter
collection, and the network policy can grant internet access to those two pods
and nothing else.

It also covers the assignment requirement to programmatically connect to and use
a REST API.

#### C4: Optimizer Service

*Responsibility:* the business logic. Fetch consumption from C2 and prices from
C3 in parallel, decide which hours need relief, move deferrable load into cheaper
hours, and report savings, peak reduction, carbon and a ranked list of
recommendations. It also runs the anomaly detector.

*Why it exists separately:* CPU-bound and stateless, which makes it the easiest
thing here to scale and the first thing that will need it. Any replica can answer
any request.

*How load is placed.* This took three attempts and the first two were wrong, so
it is worth setting out properly.

The obvious approach is to move deferrable load into the cheapest hours, cheapest
first. That is wrong in a way that costs money rather than merely leaving some on
the table.

A hospital pays two electricity bills. One is energy, in kWh, priced by the hour.
The other is the grid demand charge (*effektavgift*), billed on the single
highest hour of the month.

The model uses **34 SEK/kW/month**, the top of Vattenfall Eldistribution's base
band for high-voltage business subscriptions, which runs 11-34 SEK/kW/month.
Skanska Energi charges 265 SEK/kW/year, about 22 SEK/kW/month. Vattenfall also
levies a high-load surcharge of 21-86 SEK/kW/month in January, February, March,
November and December, which this model ignores, so the demand-side figures here
are conservative for a customer on that tariff. If every zone independently moves its load to the
cheapest hour, they all pick the same hour, and between them they build a new
peak taller than the one just removed. The energy bill falls and the demand
charge rises by more. On a day when cheap power happens to arrive during the
site's busiest hours, that is a large net loss.

So placement happens in two passes:

1. **Removal.** Every deferrable zone gives up its movable share of the hours
   being relieved. Nothing is placed yet.
2. **Placement.** The site profile after removal is known, so the optimizer fills
   the day up to a site-wide ceiling, cheapest hours first, never letting any
   hour exceed it. This is water filling, the usual way of posing peak
   minimisation.

Splitting the passes is what makes the second one possible. Where load lands is a
decision about the whole site and cannot be taken one zone at a time.

The obvious ceiling is the lowest feasible one, since that gives the flattest day
and the largest peak cut. That was my second mistake, and it runs in the opposite
direction to the first. Squeezing the ceiling down fills the cheap hours
completely, so everything placed afterwards has to go somewhere dearer. The peak
reduction looks excellent while the energy saving collapses, and the zones placed
last get pushed into genuinely costly hours. On a live SE4 day this gave a 469 kW
peak cut alongside an energy saving of 1.9%, and individual recommendations that
lost money. The dashboard was advising the user to run the laundry in the evening
peak.

The optimizer therefore searches for the ceiling rather than assuming one. It
sweeps forty-one candidates from the lowest feasible ceiling up to the baseline
peak, prices each resulting plan on both bills, and keeps whichever is worth most
over a year. Placement is O(zones x 24), so the sweep costs microseconds, and it
removes a trade-off that would otherwise need retuning for every price curve. On
the same day it brought the energy saving back to 11.4% while still cutting the
peak by about 420 kW.

Placement considers all twenty-four hours, preferring cheap ones, and that detail
buys a guarantee rather than a hope. Today's actual profile is itself a valid
arrangement that fits under today's peak, so a ceiling equal to the baseline peak
is always feasible and the search can never return anything higher. The optimizer
cannot raise the site peak. That follows from the method; it is not a case tested
for and patched.

Three rules govern how the result is reported:

- The peak figure is signed. An earlier version clamped it at zero, so a plan
  that raised the peak displayed as "0 kW saved" rather than as the problem it
  was.
- A move that costs money on energy while paying for itself on the demand charge
  is described in words. The dashboard never prints "Saves -31 kr".
- If the energy bill would rise by more than the demand charge falls, the plan is
  not recommended at all. The dashboard leads with "change nothing today" and
  shows the net loss. Claiming a saving on a day when there is none is worse than
  saying nothing, because somebody acts on it.

#### C5: Assistant Service

*Responsibility:* turn a question typed in English into a grounded answer. It
classifies the question against a set of known intents, calls whichever of C2, C3
and C4 can answer it, and writes the reply from the numbers those services
return.

*Why it exists separately:* it is the one component where a wrong answer would
not look wrong. Every other service returns JSON that is either right or visibly
broken; this one returns fluent prose, which reads the same whether or not it is
true. Keeping it separate lets the rule be stated exactly and then checked, and
the rule is that the assistant computes nothing. If it quotes a saving, that
number came from the optimizer's API during that request.

It is read-only by construction, with no database credentials and no write path,
so no phrasing of any question can change hospital data. An optional LLM step may
reword an answer that has already been computed; if it is absent or fails, the
deterministic answer is served unchanged. That is why the demo needs no API key.

#### C6: Forecast Service

*Responsibility:* say what to do tomorrow. It fetches the weather forecast from
Open-Meteo, converts it into predicted per-zone load through a degree-hour model,
fetches tomorrow's prices from C3, and posts the predicted day to C4's scenario
endpoint to get a plan back.

*Why it exists separately:* every other service describes what already happened.
This is the only one describing what has not. A report tells an estates manager
the bill was high; a plan tells them to start the laundry at 02:00 tomorrow.

*The design decision worth defending:* it holds no copy of the optimisation
algorithm. It could have, and an earlier sketch did. Instead it sends the
predicted day to the optimizer and asks the same question the dashboard asks
about today, so today's report and tomorrow's plan come out of one implementation
and cannot drift apart. Drift between a live view and a predictive view is what
usually kills this kind of feature, and it stays invisible until a customer finds
it.

The service is explicit about uncertainty. Every plan carries a `confidence`
field and a list of `caveats`, because tomorrow's day-ahead prices do not exist
until the market publishes them in the early afternoon. Before then it says so
rather than presenting a model output with the same confidence as a measurement.

#### C7: MongoDB

*Responsibility:* durable storage of meter readings.

*Why MongoDB:* meter readings are schemaless time-series documents, arriving in
volume, written far more often than updated, and read back through aggregations.
A document store fits that without a migration every time a new meter type
appears. It runs as a StatefulSet with a PersistentVolumeClaim, so the data
outlives the pod.

### 2.3 Architecture patterns used

| Pattern | Where | Why it is there |
|---|---|---|
| **API Gateway** | `gateway` | One public entry point; cross-cutting concerns applied once; internal services stay private. |
| **Database per Service** | `ingest` owns MongoDB exclusively | Nobody else may touch the database, not even by knowing the password, because a NetworkPolicy blocks the connection. Services stay independently deployable. |
| **Backend for Frontend (BFF)** | `gateway` reshapes and proxies | The browser gets one same-origin API; internal service boundaries can change without breaking the UI. |
| **Service Discovery** | Kubernetes DNS (`http://ingest-service:8080`) | No IP address appears anywhere in the code or config. Pods can move, restart and multiply freely. |
| **Client-side load balancing via Service** | every ClusterIP Service | Scaling a deployment automatically spreads traffic. Callers need no knowledge of replica count. |
| **Cache-Aside** | `price` caches for 15 min | Turns hundreds of calls to a third-party API into four per hour. Cheaper, faster, and a good citizen. |
| **Graceful degradation / fallback** | `price` serves stale cache, then a modelled curve | An upstream outage degrades one number's accuracy instead of blanking the dashboard. |
| **Retry with backoff** | `ingest` → MongoDB | Start-up order is not guaranteed in Kubernetes. The service waits patiently instead of crash-looping. |
| **Health / readiness separation** | all six services | Liveness failure = restart me. Readiness failure = stop sending me traffic but let me recover. Confusing the two causes restart storms. |
| **Grounded assistant / tool use** | `assistant` calls C2, C3, C4 for every figure | The component that speaks in sentences is forbidden from doing arithmetic. Fluent prose is persuasive whether or not it is correct, so the only safe design is one where it has nothing to be wrong about. |
| **Single source of truth for logic** | `forecast` posts scenarios to C4 rather than re-implementing it | Today's report and tomorrow's plan come out of one algorithm. Duplicating it would guarantee they eventually disagree, and the disagreement would surface in front of a customer. |
| **Bulkhead & fail-fast** | gateway's 8 s upstream timeout | A slow service returns a clear 502 instead of hanging every browser connected to the dashboard. |
| **Stateless compute** | `optimizer`, `price` | The precondition for horizontal scaling. No session state, no sticky routing, no coordination. |
| **Externalised configuration** | ConfigMap + Secret | One image runs in every environment (Twelve-Factor). Credentials are never in source control. |
| **Sidecar-free, single-concern containers** | all | One process per container, PID 1 handles SIGTERM, graceful shutdown on scale-down. |
### 2.4 How a single request flows

When the estates manager opens the dashboard:

1. The browser requests `/` from the **gateway**, which serves the single-page
   dashboard and sets a Content-Security-Policy plus four other security headers.
2. The page's JavaScript calls `GET /api/optimize?area=SE4` on the gateway. It
   never speaks to any other service.
3. The gateway rate-limits the caller, then forwards to
   `http://optimizer-service:8080/api/optimize`. Kubernetes DNS resolves that to
   one of the optimizer pods.
4. The optimizer issues two concurrent requests, `GET /api/summary` on
   `ingest-service` and `GET /api/prices` on `price-service`. Two 200 ms calls
   take 200 ms rather than 400 ms.
5. The ingest pod runs a MongoDB aggregation over the last 24 hours and returns a
   per-zone hourly matrix. The price pod returns a cached or freshly fetched
   curve.
6. The optimizer computes the plan and returns it, naming the pods of all three
   services that took part. That is what lets the dashboard show, rather than
   claim, that requests are spread across replicas.
7. The gateway relays the response. Round trip is typically well under a second.

---

## 3. Deployment architecture

### 3.1 Kubernetes objects

| File | Objects | Purpose |
|---|---|---|
| `00-namespace.yaml` | Namespace | An isolation boundary; `kubectl delete namespace medimatrx` removes everything. |
| `01-config-and-secrets.yaml` | ConfigMap, Secret | All configuration and credentials, outside the images. |
| `02-mongodb.yaml` | headless Service, StatefulSet + volumeClaimTemplate | Stable identity + persistent 2 GiB disk. |
| `03-ingest.yaml` | ClusterIP Service, Deployment | 2 replicas, internal only. |
| `04-price.yaml` | ClusterIP Service, Deployment | 2 replicas, internal only. |
| `05-optimizer.yaml` | ClusterIP Service, Deployment | 2 replicas, internal only. |
| `06-gateway.yaml` | **NodePort** Service, Deployment | The public entry point on port 30080. Ingress alternative included, commented. |
| `07-autoscaling.yaml` | 6 × HorizontalPodAutoscaler, 5 × PodDisruptionBudget | Independent autoscaling; protection against administrative eviction. |
| `08-network-policy.yaml` | 12 × NetworkPolicy | Default-deny east-west firewall inside the cluster. |
| `09-assistant.yaml` | ClusterIP Service, Deployment | 2 replicas, internal only. |
| `10-forecast.yaml` | ClusterIP Service, Deployment | 2 replicas, internal only. |
40 Kubernetes resources in total, all validated against the upstream JSON schemas
with `kubeconform --strict`.

### 3.2 How the horizontal scaling requirement is satisfied

Every microservice has its own HorizontalPodAutoscaler, with its own metric,
target and ceiling. Nothing is shared, so nothing couples their behaviour:

| Service | min | max | Scales on | Why this ceiling |
|---|---|---|---|---|
| `gateway` | 2 | 10 | CPU 60% | I/O-bound proxying; grows with concurrent dashboard users. |
| `ingest` | 2 | 12 | CPU 65% **and** memory 75% | Grows with the number of meters reporting; buffers documents in memory on bulk writes, so memory matters too. |
| `price` | 2 | **4** | CPU 70% | Deliberately capped low, each replica keeps its own cache, so more pods means more calls to somebody else's public API. |
| `optimizer` | 2 | **15** | CPU 55% | Pure stateless computation; the highest ceiling in the system. |
| `assistant` | 2 | 10 | CPU 60% | Scales with the number of people asking questions, which is a function of users rather than of meters. |
| `forecast` | 2 | **4** | CPU 65% | Capped low for the same reason as `price`: each replica calls a third-party weather API, and a plan is produced once a day, not once a click. |
The low ceilings on `price` and `forecast` are a decision rather than an
oversight. Scaling out a service that caches somebody else's public API does not
make the system faster; it multiplies the load on that third party and lowers the
cache hit rate at the same time. What a service depends on sets its ceiling, not
how much traffic you would like it to take.

The scale-up and scale-down `behavior` blocks are asymmetric on purpose: react
immediately when load arrives, shrink over a 180-300 second window so a short
lull does not make pods thrash.

**Demonstration:** `scripts/3-demo-scaling.ps1` scales the optimizer from 2 to 6
replicas, shows the other five deployments unchanged, then issues 20 requests and
prints which optimizer pod answered each.

### 3.3 How the persistent storage requirement is satisfied

MongoDB runs as a StatefulSet with a `volumeClaimTemplates` entry requesting 2
GiB `ReadWriteOnce`. That creates a PersistentVolumeClaim which is not deleted
when the pod is.

`storageClassName` is left out so the cluster's default class applies:
`hostpath` on Docker Desktop, `standard` on Minikube, an EBS or Azure Disk volume
on a cloud provider. The same YAML therefore deploys unchanged in all three.

**Demonstration:** `scripts/4-demo-persistence.ps1` records the stored total,
deletes `mongodb-0` outright, waits for Kubernetes to recreate it, and shows the
same total afterwards.

---

## 4. Benefits of this architecture

**Scaling that matches the real cost drivers.** Ingest scales with meter count,
the optimizer with site count, price with neither. In a monolith you would scale
all of it to relieve any of it, which on a cloud bill is the difference between
paying for what you use and paying for your worst component.

**Independent deployment and independent failure.** The optimisation algorithm is
the part that will change most often and where the intellectual property sits.
Because it is a separate service with no database, it can be redeployed several
times a day with a zero-downtime rolling update, while meter collection, which
must never lose a reading, is touched rarely. A bug in a new algorithm cannot
corrupt stored data, because the optimizer has no write access to anything.

**Polyglot by design.** The two data-handling services are Node.js, where
non-blocking I/O is the natural fit. The two computational services are Python,
where the numerical and machine-learning ecosystem lives. Choosing per service is
only possible because the contract between them is HTTP and JSON rather than a
shared runtime.

**Degradation instead of failure.** If `elprisetjustnu.se` is down the price
service serves its stale cache, and failing that a modelled curve, labelled as
modelled. The hospital still sees its consumption, its anomalies and an
approximate plan. A monolith calling that API synchronously would typically have
shown an error page.

**Self-healing and safe releases.** Liveness probes restart hung containers,
readiness probes take sick pods out of rotation without killing them,
`maxUnavailable: 0` means no capacity is lost during a release, and
PodDisruptionBudgets stop an administrator draining a node from taking the last
replica with it.

**The business case.** On the demo dataset the system finds about 11% off the
daily energy bill plus a reduction in the grid demand charge, worth for one
hospital somewhere between 1.30 and 1.42 MSEK a year (median 1.40), against a
hosting cost measured in hundreds of SEK a month. The range matters, and the
measurements behind it are below. As SaaS, one deployment serves many hospitals and the
marginal cost of the next customer is a few more optimizer pods. It also produces
an auditable carbon-reduction figure, which matters in Swedish public-sector
procurement.

### Measured results

The demo meter data is regenerated with fresh random noise on every run, so a
single figure would be misleading. Over twelve independent runs against live SE4
prices:

| Metric | Median | Range |
|---|---|---|
| Daily energy saving | 11.4% | stable to this precision |
| Site peak reduction | 423 kW | 164 - 439 kW |
| Annual benefit, both tariff components | 1.40 MSEK | 1.30 - 1.42 MSEK |

The energy saving is stable. The peak reduction is not: ten of the twelve runs
landed between 410 and 439 kW, but two came in around 165 kW because that day's
random baseline left little shaveable load near the peak hour. Quoting the best
run would overstate the peak result by nearly threefold against the worst, so the
median is used throughout this report.

The anomaly detector found both injected faults in every run.

Two caveats belong with these numbers. The modelled site consumes 11.6 GWh a year
at an average 0.94 SEK/kWh, which is **spot energy only**; a real invoice also
carries network charges and energy tax, so the saving as a percentage of the
whole bill would be smaller than the percentage quoted against the energy
component. And the demand-charge saving assumes the daily peak reduction is also
achieved on whichever day sets the month's maximum, because that one hour is what
the tariff bills. Across a month of similar days that is reasonable, but it is an
assumption, not a measurement.

---

## 5. Challenges and what was done about them

### 5.1 Distributed systems are harder than a monolith

**The challenge.** One call to `/api/optimize` becomes three network hops. Every
hop can be slow, can fail, or can succeed slowly, which is worse. No stack trace
spans all six services.

**What was done.** Every service emits single-line structured JSON logs tagged
with service and pod name, so `kubectl logs` output can be filtered and
correlated. The `/api/optimize` response carries a `servedBy` block naming every
pod that took part, so any answer can be traced back. The gateway applies a hard
8-second timeout to every upstream call and returns a clear 502 rather than
hanging.

**What remains.** There is no distributed tracing. The right answer is
OpenTelemetry with a trace ID propagated through every hop into Jaeger or Tempo,
plus Prometheus metrics and Grafana. That is the first thing to add before this
carries production traffic.

### 5.2 Eventual consistency and cache staleness

**The challenge.** Prices are cached for 15 minutes. A reading written to MongoDB
is not instantly visible in a summary computed a second earlier. The dashboard
can therefore show a plan up to 15 minutes stale.

**What was done.** The TTL is a trade-off taken knowingly: prices are published
day-ahead and change hourly, so 15 minutes of staleness is harmless. The UI
labels the price source and marks it when stale or modelled.

**What remains.** For sub-minute freshness the price service would push updates
over a message bus instead of being polled.

### 5.3 The database is a single point of failure

**The challenge.** One MongoDB pod. If its node fails, meter storage stops.

**What was done.** The assignment states the database need not be scalable, so
this was accepted knowingly rather than by oversight. The ingest service retries
its connection indefinitely with backoff and reports itself not ready while the
database is unreachable, so Kubernetes stops routing to it instead of letting it
return errors. The rest of the system stays up; price data and the dashboard keep
working.

**What remains.** Production needs a three-member replica set across availability
zones, automatic elections, scheduled backups to object storage, and a restore
procedure that has actually been tested. The reclaim policy should be `Retain` so
deleting the application cannot delete the data.

### 5.4 Rate limiting is per-pod, not global

**The challenge.** The gateway's limiter counts requests in one pod's memory. With
4 gateway replicas the effective limit is four times the configured one.

**What was done.** It is documented in the code where it is implemented, rather
than being quietly wrong.

**What remains.** Move the counter to Redis, or better, move rate limiting to the
ingress controller where it belongs. The commented Ingress in `06-gateway.yaml`
includes an `nginx.ingress.kubernetes.io/limit-rps` annotation showing this.

### 5.5 Operational complexity

**The challenge.** A monolith is one process. This is 40 Kubernetes objects, six
container images, and a build pipeline. For a two-person startup that is real
overhead, and for a single hospital it is a bad trade.

**What was done.** A `docker-compose.yml` runs the whole system locally in about
30 seconds with no cluster, which makes it possible to tell code problems apart
from cluster problems. The numbered PowerShell scripts make build, deploy and
teardown one command each.

**What remains.** Package as a Helm chart so environments differ by a values file;
add CI that builds, tests and pushes on every commit; add ArgoCD or Flux for
GitOps deployment.

---

## 6. Security

Taken in the order an attacker would meet it.

### 6.1 What was done

**One door, not six.** Only the gateway has a NodePort. Ingest, price, optimizer,
assistant and forecast are all ClusterIP with no address reachable from outside
the cluster. Five of the six services cannot be attacked from the internet at
all.

**Authentication and authorization, in separate services.** Every `/api` route
but registration, verification and login requires a bearer token; the gateway
checks it against `authorization-service` before proxying anywhere, and the
two concerns are deliberately split across two services rather than one, so a
permissions change never touches the code that checks a password and vice
versa. `auth-service` owns the only two MongoDB collections that hold identity
data (`users`, `verification_codes`); `verification-service` holds none of its
own, so it can run at any replica count without a shared cache. A `staff`
account may read and write; a `viewer` account is read-only, enforced by
`authorization-service`, not by the browser.

**Default-deny network policy.** Kubernetes lets every pod talk to every other
pod unless told otherwise. `08-network-policy.yaml` reverses that: a
`default-deny-all` policy blocks everything, then twelve policies open only the
conversations the system needs. The important one is `mongodb-ingress`, where
only pods labelled `app: ingest` may open a TCP connection to MongoDB. An
attacker holding the database password from a compromised optimizer pod would
still find the network refusing the connection. That is lateral-movement
containment, and it is the difference between one compromised pod and a
compromised cluster.

Egress works the same way. Only `price` and `forecast` may reach the public
internet, only on port 443, and both policies exclude the private RFC 1918
ranges. Even fully compromised, neither could be used to scan the cluster's
internal network.

**Least-privilege containers.** Non-root user (`runAsNonRoot: true`),
`allowPrivilegeEscalation: false`, all Linux capabilities dropped, read-only root
filesystem. A compromised process cannot write a payload to disk. A small
`emptyDir` is mounted at `/tmp` for legitimate temporary files.

**Resource limits on every container.** Without them one runaway container can
starve every other pod on the node. This is denial-of-service protection as much
as capacity planning.

**Input validation at the boundary.** `POST /api/readings` rejects unknown zone
IDs, non-numeric or out-of-range kWh values and malformed timestamps, returning
400 with a clear message. The price service constrains `area` with the regular
expression `^SE[1-4]$` and the window length to 1-12 through FastAPI's
validators. MongoDB is only ever addressed through the driver with parameterised
documents, never by concatenating strings into a query, so NoSQL injection is not
reachable.

**Output encoding in the browser.** Every value rendered into the dashboard goes
through an HTML-escaping function, so a hostile zone name or fault message cannot
become script. With the Content-Security-Policy header, stored XSS is closed from
both ends.

**Security headers.** The gateway sets `Content-Security-Policy` (restricting
scripts, styles, images and connections to same-origin), `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and a
`Permissions-Policy` disabling camera, microphone and geolocation. It also
disables `X-Powered-By`, which otherwise advertises the framework version to
anyone scanning.

**Rate limiting.** 600 API requests per IP per minute at the gateway, returning
429 with a `retryAfterSeconds` field in the response body.

**No credentials in source control.** The database username and password live in
a Kubernetes Secret and arrive as environment variables. Nothing in the Git
repository contains a working credential.

**Safety as a security property.** The optimizer refuses to shift, reduce or
delay any zone flagged `critical`, and every response states this explicitly. In
a clinical setting an optimisation that could throttle an operating theatre is
not a feature with a bug, it is a patient-safety incident. The safest place for
that rule is in code, on the server, where no interface can bypass it.

### 6.2 What is deliberately not done, and how to fix it

| Weakness | Risk | Mitigation |
|---|---|---|
| **Role is self-declared at registration**, not administrator-approved. `staff`/`viewer` is chosen by the person signing up. | A new account can grant itself write access. | An admin-approval step, or an invite-only registration flow, before granting the `staff` role. |
| **No mail server**, so a verification code is returned directly in the register/login response rather than emailed. | Anyone who intercepts the response also gets the code. | Plug a real mail provider into verification-service; auth-service and the gateway would not need to change. |
| **Kubernetes Secrets are base64-encoded, not encrypted.** Anyone who can read Secrets in the namespace can read the password; they are stored in etcd. | Credential disclosure via a cluster-level compromise or an over-permissive RBAC role. | Enable encryption-at-rest for etcd; better, use an external vault (HashiCorp Vault, AWS Secrets Manager, Azure Key Vault) through the Secrets Store CSI driver, with short-lived automatically rotated database credentials. |
| **All traffic inside the cluster is plain HTTP.** | An attacker with network access could read or modify traffic between pods. | A service mesh (Istio or Linkerd) providing automatic mutual TLS between every pod, plus TLS termination with a real certificate at the ingress (cert-manager + Let's Encrypt). |
| **NetworkPolicies are not enforced on Docker Desktop.** Its default CNI ignores them, so the objects exist but block nothing locally. | False sense of security when demonstrating locally. | Deploy on a cluster with Calico or Cilium, where the same YAML is enforced. This is stated honestly rather than claimed as active protection. |
| **The database password is in the repository** as a placeholder value. | If deployed as-is, the password is public. | It is clearly marked `ChangeMeBeforeProduction_2026`, and in a real pipeline the Secret would be generated at deploy time and never committed. |
| **No image scanning or signing.** | A vulnerable base image or a tampered image could be deployed. | Trivy or Grype in CI to fail the build on high-severity CVEs; Cosign signatures verified by an admission controller; pin base images by digest rather than tag. |
| **No audit logging.** | No record of who changed what. | Kubernetes audit policy shipped to a SIEM; application-level audit events for any write. |
| **Rate limiting is per-pod.** | The real limit is (limit × replicas). | Redis-backed counter, or rate limiting at the ingress. |
| **GDPR.** Energy data is not personal data, but occupancy patterns inferred from a small ward can become personal data. | Regulatory exposure. | Aggregate to zone level (already done), define a retention policy, and run a DPIA before any per-room metering. |
### 6.3 The most important thing on this list

If MediMatrx went to a real hospital tomorrow, authentication and TLS are the two
gaps that would have to close before anything else. Everything else in that table
is defence in depth. Those two are the front door standing open.

---

## 7. Conclusion

MediMatrx meets the assignment's technical requirements: six independently
scalable microservices in two languages, each with its own REST API, a MongoDB
database on persistent storage, browser access from outside the cluster, images
published to Docker Hub, and a complete Kubernetes deployment. It does so while
solving a problem a Swedish hospital actually has.

The justification for the architecture is not that microservices are
fashionable. It is that the three workloads here (high-volume writes,
third-party data acquisition, and CPU-bound optimisation) grow at different rates
as the customer base grows, and only a distributed architecture lets each be paid
for separately.

The part I would carry into other work is narrower and concerns the optimisation
itself. Two implementations in a row were arithmetically correct and economically
wrong, in opposite directions, because each attended to one tariff component and
not both. The first was only visible because the peak change is reported as a
signed quantity; an earlier version clamped it at zero and displayed a 938 kW
regression as "0 kW". Instrumentation that rounds a regression towards
reassurance hides exactly the faults worth finding.

---

## Appendix A: Complete REST API

All endpoints are reachable through the gateway at `http://localhost:30080`.

### Ingest Service

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/zones` | The nine metered hospital zones and their classification. |
| `POST` | `/api/readings` | Store one meter reading. Body: `{"zoneId":"icu","kwh":148.2,"ts":"..."}` |
| `GET` | `/api/readings?zone=&limit=` | Raw readings, newest first. |
| `GET` | `/api/summary` | Per-zone, per-hour consumption for the last 24 hours. |
| `POST` | `/api/simulate` | Generate a realistic demo day (add `?faults=0` to omit the injected equipment faults). |

### Price Service

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/prices?area=SE4` | Today's 24 hourly prices, live from elprisetjustnu.se. |
| `GET` | `/api/prices?area=SE4&day=tomorrow` | Tomorrow's day-ahead prices, once the market has published them. |
| `GET` | `/api/prices/cheapest-window?hours=3` | The cheapest run of N consecutive hours. |
| `GET` | `/api/stats` | Cache hit counters and upstream call counters. |

### Optimizer Service

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/optimize?area=SE4` | The full optimisation plan, savings and ranked recommendations. |
| `GET` | `/api/optimize?flex=laundry:0.9` | The same plan under a what-if flexibility override. Clinical zones are rejected. |
| `POST` | `/api/optimize/scenario` | Optimise a supplied day rather than today's measured one. This is how the forecast service reuses the algorithm instead of copying it. |
| `GET` | `/api/anomalies` | Equipment faults detected in the last 24 hours. |

### Assistant Service

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Ask a question in plain English. Body: `{"message":"which zone should I shift first?"}` Returns the answer, the classified intent, and the services consulted. |
| `GET` | `/api/chat/suggestions` | Starter questions for the dashboard's chat panel. |

### Forecast Service

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/forecast/weather` | Today's and tomorrow's hourly temperature for the site. |
| `GET` | `/api/forecast/plan?area=SE4` | Tomorrow's predicted load and the optimisation plan for it, with a confidence rating and explicit caveats. |

### Operational

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness, on every service. |
| `GET` | `/readyz` | Readiness, on every service. |
| `GET` | `/api/topology` | Which pod is currently serving each service. |

---

## Appendix B: Technology choices

| Choice | Alternative considered | Why this one |
|---|---|---|
| Node.js for ingest & gateway | Python for everything | Non-blocking I/O suits high-volume writes and proxying; smallest possible container. |
| Python/FastAPI for price & optimizer | Node.js | The numerical and future ML ecosystem; FastAPI gives automatic OpenAPI docs and request validation for free. |
| MongoDB | PostgreSQL / TimescaleDB | Schemaless time-series documents; no migration when a new meter type appears. TimescaleDB would be the better choice at very large scale. |
| REST/JSON between services | gRPC, message queue | Readable in a browser and with `curl`, trivially debuggable, and the assignment asks for REST. gRPC would be faster; a queue would decouple ingest from storage. |
| StatefulSet for MongoDB | Deployment + PVC | Stable network identity and a guaranteed one-to-one pod-to-volume binding. |
| NodePort for external access | LoadBalancer, Ingress | Works identically on Docker Desktop, Minikube and any cloud, with no extra controller to install. An Ingress definition is included for production. |
