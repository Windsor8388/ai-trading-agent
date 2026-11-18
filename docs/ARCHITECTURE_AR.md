# التصميم المعماري لنظام Trading Intelligence

**الإصدار:** 0.4.0 · **الوضع التشغيلي الحالي:** `ANALYSIS_ONLY` محلي وبدون أي data feed للنظام متصل. يوجد CSV importer وhistorical replay وPaper simulator وrisk/performance analytics وexecution lifecycle محلية، لكنها لا تتصل بسوق أو وسيط. توجد لوحة static مع رسم TradingView خارجي اختياري ومعزول؛ العرض ليس data feed ولا input للمحركات.
التصميم يفصل عمداً البحث والتحليل والورق والتنفيذ. لا يجوز اختصار هذه المسارات لمجرد أن جميعها تستخدم كلمة "signal".

## 1) المعمارية المستهدفة

```mermaid
flowchart LR
    A[Data providers
    Spot / GC futures / trades / L2 / macro] --> B[Provider adapters]
    B --> C[Raw immutable store
    source + checksum + retrieval time]
    C --> D[Normalization & Data QA
    UTC, dedupe, gaps, sessions, symbol identity]
    D --> E[Feature / Knowledge retrieval]
    D --> F[Time-aware market snapshot]
    F --> G1[ICT engine]
    F --> G2[SMC engine]
    F --> G3[Wyckoff / Price Action / S&D]
    F --> G4[Volume / Profile / Order-flow]
    F --> G5[Quant / Fundamental / ML]
    E --> G1
    E --> G2
    E --> G3
    E --> G4
    E --> G5
    G1 --> H[Evidence registry
    correlation groups + provenance]
    G2 --> H
    G3 --> H
    G4 --> H
    G5 --> H
    H --> I[Central Trading Intelligence
    regime, de-correlation, NO TRADE]
    I --> J[Risk Management Engine]
    J --> K[Signal lifecycle
    analysis -> paper -> gated execution]
    K --> L[Paper simulator / Broker adapter]
    L --> M[Orders, fills, positions, audit log]
    M --> N[Evaluation & monitoring]
    N --> O[Experiment registry / promotion gates]
```

### حدود الثقة والأمان

| الحد | مسؤولية | حظر صريح |
|---|---|---|
| Provider adapter | تحويل رد المزود كما هو وحفظ raw lineage | لا يخترع OHLC أو حجم أو timezone |
| Normalizer/QA | identity، UTC، complete bars، gaps، quality score | لا يمزج XAU/USD وGC أو BID/ASK/MID |
| Snapshot | منح المحرك فقط ما كان متاحاً عند `as_of` | لا future bars ولا revised news بلا timestamp |
| School engine | مفهوم واحد/مجموعة مفاهيم مدروسة وevidence قابل للإعادة | لا يحدد حجم الصفقة أو يرسل أمراً |
| Central engine | de-correlation/regime/conflict/NO_TRADE | لا يعتبر تعدد الأسماء استقلالاً |
| Risk engine | رأس المال، limits، correlation، events، sizing | لا يغير الاتجاه لإرضاء العائد |
| Signal engine | validation + lifecycle | لا يصدر actionable signal ببيانات غير موثقة |
| Execution adapter | idempotency/order state/reconciliation/kill switch | لا live access قبل approval مستقل |

## 2) مخطط تدفق القرار

```mermaid
sequenceDiagram
    participant P as Provider
    participant Q as Data QA
    participant S as Snapshot(as_of)
    participant E as Engines
    participant C as Central
    participant R as Risk
    participant T as Trade Signal

    P->>Q: raw data + source metadata
    Q->>Q: verify timestamps / complete / gaps / identity
    Q->>S: immutable closed bars only
    S->>E: same point-in-time data per timeframe
    E->>C: EngineReports + evidence + invalidations
    C->>C: cap correlated evidence / check HTF conflict
    alt weak, conflicting, unavailable, or incomplete
        C-->>T: NO_TRADE with reasons
    else hypothesis qualifies
        C->>R: direction + explicit levels + portfolio state
        R->>R: limits / event embargo / quantity / correlation
        alt risk fails
          R-->>T: BLOCKED / NO_TRADE
        else paper mode & verified data
          R-->>T: PAPER_CANDIDATE
        end
    end
    T-->>T: audit record; no live order in v0.1
```

`NO_TRADE` نتيجة أولى وليست خطأً أو حالة افتراضية مخفية. وفي الإصدار الأول لا يتجاوز أي قرار بوابة `ANALYSIS_ONLY`.

## 3) نماذج البيانات والواجهات

### مفاتيح الهوية والـlineage

```text
InstrumentIdentity
  canonical_id: XAUUSD_SPOT | GC.<expiry> | GC_CONTINUOUS.<roll_policy>
  venue/provider/symbol
  asset_kind: spot_cfd | futures | equity | crypto_spot | crypto_perp
  quote_currency, price_component: BID | ASK | MID | LAST | SETTLEMENT
  contract/multiplier/tick/lot: provider- and account-specific

RawRecord
  provider, request parameters, received_at_utc, payload hash, schema version,
  license/retention tag, parser version

NormalizedBar
  instrument_identity, open_time_utc, close_time_utc, timeframe,
  o,h,l,c, volume, volume_semantics, is_complete, quality_flags, raw_record_id
```

**قاعدة الذهب:** `XAU/USD` spot/CFD و`GC` future منتجان مختلفان. للعقد GC (COMEX) وحدة 100 troy ounces وحد أدنى $0.10/oz بحسب rulebook CME؛ وتؤكد CME ذلك أيضاً في overview المنتج [SRC-CME-GC-RULEBOOK](../knowledge/sources.json) [SRC-CME-GC-OVERVIEW](../knowledge/sources.json). فرق الاستحقاق والـroll وbasis والجلسة ومصدر الحجم يمنع استبدال أحدهما بالآخر. يمكن استخدام GC كـcontext لفرضية مستقلة بعد محاذاة/اختبار، لا كأنه شمعة XAU/USD.

### العقد الداخلي الحالي (موجود في الكود)

| object | الملف | الحقول/الوظيفة الأساسية |
|---|---|---|
| `Candle` | `models.py` | OHLCV وفتح/إغلاق timezone-aware مع invariants |
| `FeedMetadata` | `models.py` | provider, instrument kind, price component, mode, source URL, verified/quality |
| `MarketSnapshot` | `models.py` | `as_of` + bars مقفلة فقط حسب timeframe |
| `Evidence` | `models.py` | score [-1,1]، confidence، correlation group، measurement، concept ID |
| `EngineReport` | `models.py` | direction، regime، أدلة/أدلة مضادة/إلغاء/مدخلات ناقصة |
| `Decision` | `models.py` | action، أدلة مختارة ومرفوضة لارتباطها، أسباب/invalidation |
| `RiskAssessment` | `models.py` | approval، quantity، cash risk، reward/risk، أسباب |
| `TradeSignal` | `models.py` | signal candidate ممهور بالمصدر والمخاطر والحالة؛ ليس order |
| `DatasetManifest` | `providers/csv_ohlcv.py` | SHA-256 ومصدر CSV/parser/quality والـfeed lineage |
| `BacktestResult` | `backtest/engine.py` | trades، equity curve، costs، أسباب الخروج وملخص منفصل للـtrade/bar |
| `ResearchLedger` | `research/ledger.py` | JSONL hash-chain للتنبؤ/التجربة قبل ظهور النتيجة |
| `PaperExecutionSimulator` | `paper.py` | محاكاة محلية idempotent/kill-switch وليست venue |
| `ExecutionPolicy` | `execution/lifecycle.py` | allowlist لـproduct specs وquantity/notional/cash-risk limits؛ لا يوافق على risk فاشل |
| `OrderLifecycleManager` | `execution/lifecycle.py` | intent idempotency، disconnect=`UNKNOWN`، reconciliation، emergency stop؛ بلا شبكة |
| `VenueOrderSnapshot` | `execution/lifecycle.py` | snapshot ممهور زمنيًا وcumulative quantity لحل UNKNOWN؛ لا fill بلا كمية |

### عقود واجهات المرحلة التالية

```python
class MarketDataProvider(Protocol):
    def list_instruments(self) -> list[InstrumentIdentity]: ...
    def fetch_bars(self, request: BarRequest) -> RawResponse: ...
    def stream_quotes(self, request: QuoteRequest) -> AsyncIterator[RawQuote]: ...

class EconomicCalendarProvider(Protocol):
    def events(self, start_utc: datetime, end_utc: datetime) -> list[EconomicEvent]: ...

class ExecutionVenue(Protocol):
    def validate_order(self, draft: OrderDraft) -> ValidationResult: ...
    def submit_idempotent(self, draft: OrderDraft, client_order_id: str) -> VenueOrder: ...
    def get_order(self, client_order_id: str) -> VenueOrder: ...
    def emergency_flatten(self, approval: HumanApproval) -> list[VenueOrder]: ...
```

لن تنفذ هذه الواجهات في `LIVE` إلا بعد مرحلة approval صريحة منفصلة؛ وجود interface ليس اتصالاً ولا تفويضاً.

## 4) المحركات واستقلال المسؤولية

| المحرك | ما يملكه | ما لا يملكه | حالة v0.4 |
|---|---|---|---|
| ICT | FVG وequal-high/low sweep heuristics بإصدار | تعريف SMC أو دليل order book | منفذ تجريبي |
| SMC | structure + displacement/order-block proxy | تنفيذ ICT أو sizing | منفذ تجريبي |
| Price Action | pivots وstructure | مستويات order block | منفذ تجريبي |
| Volume | relative-volume confirmation إذا كان المعنى معروفاً | volume profile/CVD من OHLC | منفذ تجريبي |
| Quant | SMA 20/50 + volatility gate كـbaseline | claims ML/alpha | منفذ تجريبي |
| Wyckoff | state machine phase/event | — | backlog بمواصفة |
| Supply/Demand & Classical | geometry/zone detectors | — | backlog |
| Profile | TPO/VAP/session auction | footprints | backlog وfeed خاص |
| Order Flow | delta/CVD/footprint/L2 | OHLC proxy مزعوم كبيانات حقيقية | يعلن unavailable |
| Fundamental | calendar/news/macros | تفسير خبر بلا مصدر | يعلن unavailable |
| ML | calibrated models/feature store | تدريب على holdout | gated backlog |

كل `Evidence` يربط بـ`source_concept_id` في knowledge base. المركز لا يقرأ اسم المدرسة ليعطي وزناً، بل يتعامل مع القياس وcorrelation group وإصدار القاعدة ونتيجة ablation المسجلة لاحقاً.

## 5) اختيار التقنيات

| الطبقة | الآن | عند الإنتاج بعد PoC | المبرر |
|---|---|---|---|
| Core domain | Python 3.11+، stdlib/dataclasses | Python 3.12+, Pydantic v2 عند API boundaries | قابلية قراءة واختبار وخطوات research سريعة |
| Dataframes | لا dependency في v0.1 | Polars + Arrow/Parquet | أداء عمودي وschema واضح للبيانات الكبيرة |
| Storage | JSON knowledge/versioned files | object storage للraw Parquet + PostgreSQL/TimescaleDB للmetadata + DuckDB للبحث | raw immutable مقابل queryable metadata |
| Workflows | commands/tests محلية | Prefect/Dagster + queue | retries، lineage، schedules |
| API/dashboard | لا خدمة حالية | FastAPI + React/TypeScript | API typed وواجهات decision/audit |
| Event bus/cache | لا حاجة | Kafka/Redpanda + Redis | streams وidempotency بعد تبرير الحجم |
| ML experiment | لا ML | MLflow + feature registry (أو بديل أبسط أولاً) | reproducibility/model lineage |
| Observability | stdout/tests | OpenTelemetry + Prometheus/Grafana + append-only audit | latency/data quality/risk alerts |
| Deployment | لا نشر | Docker + IaC + secret manager | فصل المفاتيح والصلاحيات وتكرار البيئة |

هذه اقتراحات؛ لا توجد الحزم أو الخدمات أو مفاتيح API مثبتة في هذا الفرع حالياً.

## 6) هيكل الملفات الحالي والمستهدف

```text
knowledge/
  concepts.json                 # definitions/rules/relations/sources/version
  sources.json                  # registry of source provenance
src/trading_intelligence/
  models.py                     # domain contracts
  data.py                       # complete-bar and chronology validation
  knowledge.py                  # source-validated deterministic concept retrieval
  knowledge_data/               # packaged mirror of the canonical registry
  orchestration.py              # independent multi-TF engine runner
  central.py                    # evidence de-correlation/NO_TRADE
  risk.py                       # independent limits, gap-buffered sizing, embargo gates
  risk_book.py                  # local realized-loss/DD/exposure/correlation state
  monitoring.py                 # freshness/incident health model; no alert transport
  signals.py                    # analysis/paper safety gate
  paper.py                      # local idempotent paper state machine; no broker
  execution/lifecycle.py         # policy-gated, disconnected lifecycle + reconciliation state machine
  providers/contracts.py        # read-only market/calendar API contracts
  providers/csv_ohlcv.py        # strict user-supplied historical OHLCV importer
  backtest/engine.py            # next-open, stop-first historical replay
  engines/
    indicators.py               # transparent no-lookahead primitives
    technical.py                # first independent engines
  research/metrics.py           # metrics, bootstrap, temporal folds
  research/analytics.py         # DD/PF/expectancy and strategy/timeframe/session attribution
  research/ledger.py            # append-only SHA-256 experiment/prediction trail
tests/                          # 36 local deterministic tests (current)
docs/
  TRADING_INTELLIGENCE_RESEARCH_AR.md
  DATA_SOURCES_AR.md
  VALIDATION_AR.md
  EXECUTION_SAFETY_AR.md
  DASHBOARD_AR.md

dashboard/                      # static RTL control room; opt-in sandboxed third-party chart only
```

المستهدف لاحقاً يضيف `providers/`, `normalization/`, `feature_store/`, `backtest/`, `paper/`, `execution/`, `api/`, `dashboard/`, `migrations/` و`infra/`، مع عدم وضع أسرار أو raw licensed data في Git.

## 7) التنفيذ المرحلي ومعايير البوابة

| المرحلة | التسليم | بوابة الخروج (لا ادعاء نجاح قبل تحققها) |
|---|---|---|
| 1. المعرفة والبحث | registry + concepts + policy | مراجعة مصدر/إصدار لكل rule، وفصل definition عن evidence |
| 2. محركات المدارس | detectors مستقلة وfixtures | unit/replay tests وconcept mapping لكل output |
| 3. العقل المركزي | calibration/de-correlation/regime | ablation وcorrelation analysis على OOS فقط |
| 4. البيانات | CSV historical importer + hash manifest + resampling محافظ **(منفذ محلياً)**؛ provider API read-only لاحقاً | QA، timestamps، gaps، spot/GC identity، reconciliation مع vendor |
| 5. signals | levels/expiry/cancel conditions | لا signal عند stale/unverified/missing inputs |
| 6. risk | portfolio/exposure/news correlation **(limits وevent/circuit breakers منفذة؛ لا calendar feed بعد)** | property tests وlimits في حالات فشل متعددة |
| 7. backtest | event-driven next-open fills/cost/stop-first replay **(منفذ محلياً)**؛ roll/tick model لاحقاً | benchmark known fixtures + no-lookahead audits |
| 8. paper | append-only hash ledger + in-memory idempotent paper simulator **(منفذ محلياً)** | reconciliation وفترة forward precommitted |
| 9. improvement | locked experiment registry + model gates | final OOS untouched + drift monitoring |
| 10. live study | venue adapter + human approval؛ يوجد الآن lifecycle محلي يمنع live ويغطي validation/idempotency/reject/disconnect/reconcile/kill state | disaster drills, security review، explicit user approval، venue reconciliation؛ لا auto-go-live |

## 8) التنفيذ الحقيقي: تصميم مؤجل ومقيد

قبل أي دراسة live، يطلب النظام: حساب مخصص، صلاحيات أقل ما يمكن، idempotency key، order validation، max notional، kill switch، reconciliation عند إعادة الاتصال، event log غير قابل للمحو، وموافقة بشرية لكل تفعيل. ليس في المشروع كود يتصل بوسيط أو يستخدم credentials، وبناءً على طلب المستخدم لا يجب إضافته أو تشغيله من دون موافقة صريحة لاحقة.

## 9) المسارات التشغيلية التفصيلية: البيانات، المخاطر، الاختبار، والمراقبة

```mermaid
flowchart TD
    P[Vendor/API or user CSV] --> R[Raw payload + request metadata]
    R --> M[Manifest SHA-256 + immutable storage]
    M --> Q{Data QA
    schema / UTC / complete / gap / identity}
    Q -->|fail| I[Quality incident + quarantine + NO TRADE]
    Q -->|pass| S[Point-in-time snapshot]
    S --> E[School engines]
    E --> C[Central decision + evidence de-correlation]
    C --> RB[PortfolioRiskBook]
    RB --> RM[RiskManagementEngine]
    RM -->|blocked| L[Ledger + dashboard explanation]
    RM -->|paper candidate| PS[Paper simulator]
    S --> BT[Historical replay]
    BT --> PA[PerformanceAnalyzer
    DD / PF / expectancy / Sharpe / Sortino
    strategy / timeframe / session]
    PA --> L
    PS --> L
    L --> MON[Monitoring / alerts / read models]
```

### Risk model وواجهته

```text
PortfolioRiskBook (local PoC; durable event store in production)
  initial_equity, high_water_mark, realized pnl events, open positions
  UTC daily/weekly net realised loss
  drawdown, gross exposure, correlation concentration, consecutive losses

OpenRiskPosition
  position_id, instrument, correlation_group, gross_notional, opened_at

PortfolioState -> RiskManagementEngine.assess(...)
  equity, daily/weekly loss, drawdown, open/correlated positions,
  exposure, event_risk_active, gap_risk_active

RiskAssessment
  approval, quantity, planned cash risk, gap-buffered risk/unit,
  worst-case cash risk, reward/risk, machine-readable reasons
```

`PortfolioRiskBook` منفذ محلياً لاختبار الحسابات؛ لا يحل محل مصدر أرصدة/مراكز موثوق أو reconciliation مع وسيط. الخسارة اليومية/الأسبوعية في النسخة الحالية هي **صافي P&L محقق** ضمن يوم/أسبوع UTC، وهو convention معلن يجب أن يطابق policy المؤسسة قبل الاستخدام الحقيقي.

### التحليلات والـread models

```text
BacktestTrade / Paper trade
  strategy_id, timeframe, entry/exit UTC, costs, net PnL,
  return_on_equity, MFE/MAE, bars_held, exit reason

EquityPoint
  time UTC, equity

PerformanceAnalyzer
  overall + by_strategy + by_timeframe + by_session
  profit factor, expectancy, win rate, compound return,
  optional documented annualized Sharpe/Sortino, drawdown episodes
```

الجلسات الافتراضية تسميات UTC عامة فقط (`ASIA_UTC`, `LONDON_UTC`, `NEW_YORK_UTC`, `OFF_SESSION_UTC`). لا تمثل ساعات بورصة أو DST؛ يمرر adapter تقويم/جلسات المنتج الحقيقي قبل إنتاج تقرير رسمي.

## 10) API ولوحة التحكم والمراقبة — تصميم قبل البناء

لا توجد خدمة API أو واجهة منشورة في هذا الفرع. هذا عقد البناء المقترح، وليس ادعاء اتصال بـSupabase أو Vercel.

```mermaid
flowchart LR
    U[Researcher / reviewer] --> UI[React/TypeScript dashboard]
    UI --> API[FastAPI / typed REST or OpenAPI]
    API --> DB[(PostgreSQL / optional Supabase)]
    API --> OBJ[Object storage: raw manifests, reports]
    API --> Q[Worker queue / scheduler]
    Q --> W[Data / backtest / analytics workers]
    W --> DB
    W --> OTEL[OpenTelemetry logs/metrics/traces]
    OTEL --> G[Prometheus/Grafana or managed monitor]
    API --> RBAC[Auth + RBAC + audit trail]
```

### واجهات API المقترحة

| المجال | أمثلة read/write API | ضوابط لازمة |
|---|---|---|
| datasets | `POST /datasets/import`, `GET /datasets/{hash}`, `GET /quality-incidents` | manifest/hash/license، no raw secret |
| analysis | `POST /analysis/runs`, `GET /decisions/{id}` | `as_of`, engine versions، evidence provenance |
| risk | `GET /risk/state`, `POST /risk/limits/validate` | read-only state، role separation، never allow UI override to bypass limit |
| research | `POST /experiments`, `GET /experiments/{id}/metrics` | immutable trial ledger، final-OOS lock |
| paper | `POST /paper/orders`, `GET /paper/orders/{id}` | idempotency key، simulator only، kill switch |
| monitoring | `GET /health`, `GET /incidents`, `GET /drift` | alert acknowledgement audit |
| execution (future) | no endpoint until approval | separate service/account/credentials/human approval |

### لوحة تحكم لازمة وليست منفذة بعد

1. **Market/Data health:** feed heartbeat، freshness، gap/duplicate/schema incidents، source manifest.
2. **Analysis:** regime، reports per engine، evidence selected/discarded as correlated، invalidation وسبب `NO_TRADE`.
3. **Risk:** equity/high-water mark، DD episodes، daily/weekly limits، exposure/correlation، event/gap embargo، kill status.
4. **Research:** experiment lineage، OOS/final-holdout state، PF/expectancy/Sharpe/Sortino وslices strategy/timeframe/session.
5. **ML (بعد بنائه):** model/feature/label versions، baseline/OOS comparison، calibration/drift/missingness.
6. **Paper:** idempotency state، synthetic fills، reconciliation status؛ ولا تظهر كأنها صفقات حقيقية.

### التخزين والأمن

- **الآن:** JSON knowledge + CSV manifest + JSONL hash ledger محلياً؛ لا أسرار أو raw licensed data في Git.
- **عند الحاجة:** PostgreSQL كـsystem-of-record للmetadata/read models. يمكن أن يكون Supabase managed PostgreSQL خياراً مناسباً إذا نُفذت RLS، migrations، service-role isolation وaudit log؛ لم يُنشأ اتصال في هذا المشروع.
- **Raw market data/artifacts:** object storage بversioning/retention/license tags؛ DB يخزن metadata وhash فقط.
- **الواجهة:** Vercel قد يستضيف React dashboard إن اختير، لكنه لا يخزن مفاتيح مزود السوق ولا ينفذ أوامر. لا يوجد نشر Vercel حالياً.
- **الأسرار:** secret manager/بيئة تشغيل فقط، least privilege، key rotation، لا logs للـtokens أو payloads الحساسة.

## 11) ما يمكن بناؤه الآن وما يحتاج خارجياً

| يمكن بناؤه/تشغيله الآن محلياً | يحتاج بيانات أو خدمة خارجية قبل تشغيله |
|---|---|
| knowledge retrieval، school-engine fixtures، central de-correlation | XAU/USD/GC historical أو live API مرخّص |
| CSV validation/manifest/resampling، replay، analytics، risk-book fixtures | contract specs/lot, bid/ask/spread، calendar/holiday/roll policy |
| Monte Carlo/walk-forward/ledger، paper simulator في الذاكرة | persistent paper feed، reconciliation، alerts delivery |
| dashboard schema/API contracts وmock UI | Supabase/PostgreSQL/object storage/auth وdeployment اختيارية |
| rule/statistical baselines specification | ML feature store، labels، data volume، compute/experiment registry |
| execution interface design وkill-switch state machine | موافقة صريحة، broker account/API، security review، human approval workflow |

المصادر والـAPI المطلوبة مفصلة في [`DATA_SOURCES_AR.md`](DATA_SOURCES_AR.md)، وحوكمة ML في [`AI_ML_GOVERNANCE_AR.md`](AI_ML_GOVERNANCE_AR.md). وجود تصميم أو interface لا يعني اتصالاً أو جاهزية للتنفيذ الحقيقي.
