# الدراسة البحثية: نظام ذكاء تداول متعدد المدارس وقابل للتدقيق

**الإصدار:** 0.1.0 · **آخر تحديث:** 2026-11-18 · **الحالة:** بحث وتصميم + نموذج أولي محلي.
**حد الأمان:** لا يوجد اتصال سوقي أو حساب وسيط أو تنفيذ أوامر في هذا المستودع. أي مخرجات حالية تحليل تجريبي وليست توصية أو إشارة سوق.

## 1) أطروحة المشروع وحدوده العلمية

المنتج المقصود ليس "نموذجاً يحفظ أسماء الأنماط" ولا مجمّعاً لإشارات المؤشرات. إنه منصة بحثية تفصل بين أربع طبقات:

1. **الرصد:** ما الذي تقوله البيانات المتاحة فعلاً، ومتى صار معلوماً؟
2. **الفرضية المدرسية:** كيف يحوّل محرك مستقل قاعدة مدرسة ما إلى ملاحظة قابلة للقياس؟
3. **الاستدلال/القرار:** ما الأدلة المستقلة، وما الأدلة المكررة أو المتعارضة، وهل القرار الصحيح هو `NO_TRADE`؟
4. **التحقق:** هل صمدت فرضية مجمّدة أمام تكاليف وتواريخ وأصول وأنظمة سوق لم تُستخدم لاختيارها؟

لا يوجد في الأدبيات أو في تسمية مدرسة تداول ما ما يثبت تلقائياً وجود عائد قابل للتنبؤ. لذلك تسجل قاعدة المعرفة كل مفهوم بوصفه أحد الآتي: **تعريف/قياس سوقي**، أو **فرضية قواعدية تحتاج اختباراً**، أو **تحكم مخاطر مطلوب**. مرجع كفاءة الأسواق يمثل خط الأساس المعرفي الذي يجب أن تتجاوزه أي دعوى تنبؤية [SRC-FAMA-EFFICIENT-MARKETS](../knowledge/sources.json). كما أن تحذير CFTC من المخاطر والرافعة والتكاليف في الذهب جزء من تصميم المخاطر، لا فقرة تسويقية [SRC-CFTC-GOLD-RISK](../knowledge/sources.json).

### أسئلة لا يجوز للنظام أن يجيب عنها باختلاق

- هل توجد بيانات لحظية؟ **ليس الآن.** `FeedMetadata.live_feed_verified=false` افتراضياً.
- هل "سيولة" تعني أوامر حقيقية؟ لا يمكن لشموع OHLC إثبات ذلك؛ هي فقط بديل/فرضية عن سلوك سعري.
- هل بيانات الحجم حجم منفّذ أم tick volume أم حجم عقد مستقبلي؟ لا يُسمح بدمجها بلا وسم.
- هل اختُبرت الاستراتيجية؟ لا؛ لم يُشغّل هذا الإصدار أي Backtest على سوق حقيقي.
- هل يمكن تنفيذ أمر؟ لا؛ وضع `LIVE` مرفوض برمجياً في `TradeSignalEngine`.

## 2) خريطة المدارس إلى عائلات محركات

| العائلة / المدرسة | المفاهيم التي يجب فصلها | المدخلات الدنيا | مخرج المحرك | حالة v0.1 |
|---|---|---|---|---|
| ICT | dealing range، premium/discount، liquidity pools/sweeps، FVG، order block، breaker، mitigation، OTE، kill zones، BOS/MSS | OHLC مكتمل، جلسة/منطقة زمنية، ATR، pivots | مناطق وأحداث ممهورة بالزمن + إلغاء | FVG وsweep تجريبيان؛ الباقي في المعرفة/خارطة الطريق |
| SMC | swing/internal structure، BOS، CHoCH، MSS، order block، imbalance، inducement، liquidity | OHLC مكتمل، pivots، ATR | بنية + OB proxy + درجة | منفصل عن ICT؛ proxy ميكانيكي أولي |
| Wyckoff | accumulation/distribution، PS/SC/AR/ST، spring/upthrust، SOS/SOW، phases A–E، effort/result | OHLCV موثّق، نطاقات، جلسات | state machine احتمالي | مواصفة فقط؛ لا محرك بعد |
| Price Action | القمم/القيعان، الاتجاه، الإغلاق مقابل wick، breakout/retest، inside/outside bars | OHLC مكتمل | structure/report | محرك أولي لــ HH/HL وLH/LL |
| Supply & Demand | base، departure، freshness، touches، imbalance، zone failure | OHLC، ATR، حجم اختياري | zones + score | مواصفة فقط |
| Classical TA | trend lines، S/R، flags، triangles، H&S، double top/bottom، channels، gap | OHLC، time | قيود هندسية + breakout | مواصفة؛ S/R لاحقاً |
| Fibonacci | retracement، extension، confluence، anchor selection | pivot مؤكد | مستويات مشتقة | مواصفة؛ لا دلالة سببية مفترضة |
| Liquidity trading | equal highs/lows، stop clusters، sweep، session liquidity، book imbalance | OHLC كـproxy أو L2 حقيقي | proxy أو قياس دفتر أوامر | sweep OHLC فقط؛ يوسم كـproxy |
| Volume / VSA | relative volume، effort/result، climax، dry-up | حجم معلوم الدلالة | confirmation/counter-evidence | z-score حجم أولي؛ لا Volume Profile |
| Market Profile / Volume Profile | TPO، POC، VAH/VAL، HVN/LVN، initial balance، auction state | time-at-price أو trade-volume-at-price | profile/session state | يحتاج بيانات أدق؛ غير منفذ |
| Order Flow | footprint، bid/ask volume، delta، CVD، absorption، imbalance | tick trades + quotes أو L2 | microstructure measures | محرك عدم-توفر صريح؛ لا اختلاق من OHLC |
| Quant / Statistical | returns، volatility، momentum، mean reversion، factors، cointegration، EVT، regime | OHLCV نظيف + تكاليف | features/regime/model scores | baseline SMA/volatility فقط |
| Algorithmic | signal lifecycle، scheduling، costs، slippage، fills، portfolio constraints | بيانات + مواصفات سوق/وسيط | events/replay/audit | نماذج المجال والـrisk جاهزة، لا execution |
| Fundamental / macro | rates، USD، inflation، real yields، central banks، releases، revisions، geopolitics | تقويم/بيانات مرخّصة بنَسب | event-risk/context | غير متصل؛ لا يحلل أخباراً |
| NLP / News | entity/event extraction، surprise، sentiment، credibility، embargo | مزود أخبار مرخص + timestamps | calibrated features | مرحلة لاحقة فقط |
| ML / DL / RL | classification، forecasting، anomaly، regime، policy learning | feature store زمني + labels + folds | probabilistic model + calibration | لا نموذج ML قبل baseline وOOS lift |
| Cross-asset / derivatives | GC term structure، options، ETF flows، FX/DXY، rates | بيانات كل سوق موائمة زمنياً | context وليس بديل سعر | مرحلة لاحقة وبيانات منفصلة |

المفاهيم التفصيلية، تعريفها التشغيلي، علاقاتها، حالة الدليل، ومصادرها موجودة بصيغة قابلة للآلة في [`knowledge/concepts.json`](../knowledge/concepts.json)، وسجل المصادر في [`knowledge/sources.json`](../knowledge/sources.json). لكل مفهوم `id` ثابت و`source_ids` و`updated_at`؛ لا تستخدم واجهة المحرك تسمية حرة غير قابلة للتتبع.

### نطاق التوسعة المسجل (ليس ادعاء تنفيذ)

حتى لا تُختزل عبارة «جميع المدارس» في المدارس الشعبية فقط، يسجل backlog أيضاً: Elliott/Wave وGann وHarmonic patterns وDow theory وHeikin-Ashi/Renko/Kagi/Point-and-Figure (كتمثيلات بيانات منفصلة لا شموع عادية)، مؤشرات MA/RSI/MACD/ADX/Stochastic/Ichimoku/Bollinger/Keltner/Donchian وVWAP، momentum/trend-following/breakout/mean-reversion/volatility targeting، cross-sectional factors وpairs/cointegration/stat-arb وcarry/term-structure/seasonality، options implied-volatility/Greeks، market making/execution/TCA، correlation/network risk، sentiment/positioning/COT وon-chain للـcrypto، وcalendar/session effects. لكل واحد لا بد من concept record ومعيار بيانات واختبار مستقل قبل إدخاله كمحرك أو feature؛ بعض هذه العائلات لا تصلح للذهب الفوري أو لا تصلح من OHLC وحده.

## 3) كيف تتحول المدارس إلى قواعد قابلة للحاسوب

### 3.1 قاعدة عامة للتشغيل

أي قاعدة لا تدخل المحرك قبل أن تجيب في ملف مفهوم/استراتيجية مُصْدَر:

```text
Inputs:          نوع البيانات، تعريف الجلسة، المنطقة الزمنية، جودة المصدر
Available-at:    أول bar/tick يصبح عنده الحدث معلوماً
Parameters:      قيم محددة، نطاق بحث مسجل، وإصدار
Detection:       pseudocode حتمي أو نموذج احتمالي موثق
Confirmation:    ما الذي يجب أن يحدث بعد الاكتشاف وقبل الإشارة
Invalidation:    ما الذي يبطل الفرضية/الصفقة
Action mapping:  الدخول/الوقف/الأهداف أو لا شيء
Costs:           spread, commission, slippage, funding/roll
Evidence:        قياس IS/OOS والقيود المعروفة
```

**مثال FVG:** عند إغلاق bar `t` فقط، يكون bullish FVG إذا `high[t-2] < low[t]`، وbearish إذا `low[t-2] > high[t]`. حدود المنطقة معروفة لحظة `t`؛ لا يُسمح للرسم المتأخر بأن يعيد تاريخها. هذه قاعدة كشف، **وليست** قاعدة دخول أو دليلاً على ربحية. تنفيذها في `ICTAnalysisEngine` ومرجعها المعرفي `ict.fair_value_gap`.

**مثال structure:** pivot عند `i` لا يثبت في النسخة الحالية إلا بعد بارين مغلقين إلى اليمين (`wing=2`). تسلسل HH+HL أو LH+LL هو توصيف للحالة؛ يلزم تعريف مستقل لماهية BOS أو CHoCH قبل تحويله إلى دخول. هذا يمنع انتقاء قمم وقيعان بعد معرفة ما حدث.

**مثال footprint/delta:** لا يمكن اشتقاق delta حقيقي من شمعة OHLCV. يجب حفظ trade timestamp/price/size وbid/ask أو قاعدة signing، ونسبة الصفقات غير المصنفة. يوضح مرجع microstructure أن هذه بيانات مختلفة نوعياً عن الشموع [SRC-HASBROUCK-MICROSTRUCTURE](../knowledge/sources.json).

### 3.2 التداخل ليس استقلالاً

ICT وSMC وPrice Action تصف غالباً نفس السلسلة السعرية. لذلك تصنف أدلة البنية في المجموعة `market_structure`، ويختار القرار المركزي أقوى مشاهدة واحدة منها ويحتفظ بالباقي في `discarded_as_correlated`. لا يجوز القول إن ثلاث تسميات للـswing تساوي ثلاث تجارب مستقلة.

مجموعات الارتباط الأولية:

| المجموعة | أمثلة | سياسة التجميع الحالية |
|---|---|---|
| `market_structure` | BOS/CHoCH، OB، FVG، equal-high sweep، pivot trend | أقوى دليل مرجح واحد فقط |
| `statistical_trend` | SMA/EMA momentum، regression slope، breakout trend | واحد لكل مجموعة/نافذة محددة |
| `volume_confirmation` | relative volume، volume spike | واحد؛ ولا يؤخذ إذا لم تتضح دلالة الحجم |
| `order_flow` | delta/CVD/footprint/book imbalance | واحد أو نموذج معايرة مشترك عند توفير ticks |
| `macro_event` | scheduled release, surprise, NLP headline | يعامل كتحكم خطر قبل كونه alpha |

هذه آلية تمنع تكرار العد، لكنها ليست بديلاً عن قياس الارتباط/المعلومة الإضافية خارج العينة. المرحلة الكمية ستختبر incremental predictive value عبر ablation: baseline، ثم إضافة محرك واحد، ثم إضافة المتبقي، مع نفس التواريخ والتكاليف.

## 4) نماذج المدرسة المقترحة واختبارات القبول

### A. محركات البنية/السعر

1. **Price Action:** pivots مؤكدة، HH/HL/LH/LL، breakout close، ATR-normalized S/R. الاختبار: ثبات نتائج pivot-wing وbreak threshold في الجوار، وablation مقابل momentum بسيط.
2. **ICT:** FVG، liquidity sweep، sessions/kill-zones، dealing range، order-block/mitigation/breaker بملفات مفاهيم مستقلة. الاختبار: event study لعائدات N-bars بعد الحدث مع رسوم، ثم walk-forward؛ يمنع التحديد البصري.
3. **SMC:** internal/swing structure وBOS/CHoCH/OB/inducement. الاختبار: قياس التوافق مع Price Action أولاً؛ إذا كانا متطابقين لا يعاملان كمصدرين alpha.
4. **Wyckoff:** state machine يحافظ على حالة range ويخرج احتمالات phase لا علامات مرسومة بعد الحدث. الاختبار: inter-rater/label agreement، ثم predictive/event study.
5. **Supply/Demand وClassical وFibonacci:** detector هندسي مُعَلَّم بالـATR، anchor/range/resolution مسجل. الاختبار: multiple-hypothesis correction؛ الأنماط المرئية أكثر عرضة للتنقيب عن البيانات.

### B. محركات الحجم والبنية المجهرية

1. **Volume:** يستقبل `volume_semantics` (trade volume / tick count / futures contract volume). لا يخلطها. يقارن relative volume في نفس الجلسة إن أمكن.
2. **Profile:** يبني TPO/volume-at-price من جلسة معلنة وبـbin size ثابت؛ POC وvalue area قياسات لا أوامر.
3. **Order flow:** feed منفصل للصفقات والـquotes؛ يحسب delta/CVD من طريقة signing موثقة، ويبلغ عن unknown rate. لا يشغل إن لم تتوفر المدخلات.
4. **Liquidity:** يمزج L2/order-book فقط عندما يكون السوق نفسه؛ لا ينقل عمق GC إلى XAU/USD كأنه نفس دفتر الأوامر.

### C. المحرك الكمي والـML

ابدأ بـ baselines قابلة للشرح: اتجاه متوسطات، breakout، mean-reversion z-score، volatility targeting، session/event embargo. ثم قارن:

- **Regime:** rules شفافة (trend/range/high volatility) قبل HMM أو classifier.
- **Supervised ML:** labels ذات horizon وbarrier معلنين، features من الماضي فقط، calibration وfeature drift وpurged folds.
- **DL:** فقط إذا تجاوز نموذج أبسط على final holdout وبعد احتساب التكلفة والتعقيد.
- **RL:** ليس محرك إشارة أول؛ قد يفيد لاحقاً في execution simulator مع market impact/constraints، لا من OHLC فقط.
- **NLP:** يستخدم timestamps أصلية، revision handling، licensing وقياس latency؛ لا يفسر خبراً بعد التحرك ثم ينسب إليه السبب.
- **Anomaly:** وسيلة إنذار لجودة البيانات/التقلب أولاً، لا alpha مفترض.

العمل البحثي يطبق PBO/CSCV عندما توجد مصفوفة تجارب، وDSR عند تحليل Sharpe تحت تعدد الاختبارات [SRC-BAILEY-PBO](../knowledge/sources.json) [SRC-BAILEY-DSR](../knowledge/sources.json).

## 5) سياسة الأطر الزمنية ومنع معرفة المستقبل

| الدور | الأطر الافتراضية | وظيفة مسموحة |
|---|---|---|
| سياق هيكلي | MN, W1, D1 | regime، مناطق بعيدة، قيود اتجاهية فقط |
| سياق اتجاه | H4 | توافق/تعارض مع setup |
| setup | H1, M30 | بنية ومناطق وحالة التقلب |
| trigger | M15, M5, M1 | توقيت دخول **فقط** بعد صلاحية السياق والمخاطر |

- كل snapshot يحمل `as_of`؛ validator يرفض bar إذا `close_time > as_of`.
- لا يصبح pivot معروفاً إلا بعد بار confirmation لاحق مكتمل.
- بالنسبة للـhigher timeframe، يستخدم فقط bar المكتمل عند وقت trigger الأصغر؛ لا bar يومي ما زال يبنى.
- أخبار/تقويم: تُخزن بالـUTC وبـ`published_at` و`retrieved_at` وrevision؛ نتيجة صدرت بعد `as_of` لا تدخل feature vector.
- يجب أن يحاكي backtest تنفيذ الإشارة في bar/tick لاحق وفق سياسة fill معلنة، لا بسعر إغلاق البار الذي ولّدها إن كان غير قابل للتداول.

## 6) بروتوكول إثبات أو رفض الفرضيات

لكل `strategy_version` يسجل النظام **قبل** التشغيل: universe، مصدر/إصدار البيانات، فترة train/validation/final holdout، parameters، assumptions، تكاليف، candidate count، metric of selection، وحالة الكود hash.

1. **Data QA:** gaps/duplicates/OHLC invariants/timestamps/sessions/corporate-roll adjustments.
2. **Unit & replay tests:** أوقات اكتشاف معروفة، منع look-ahead، idempotency، مستوى وقف صحيح.
3. **In-sample research:** تستخدم فقط للتطوير؛ لا تعرض كنتيجة نهائية.
4. **Nested/walk-forward:** كل fold زمني؛ tuning داخل training فقط.
5. **Out-of-sample locked:** لم يلمسه اختيار rule/model.
6. **Robustness:** nearby parameters، instruments، timeframes، regimes، sessions، spreads/slippage أشد، delay، missing-data scenarios.
7. **Statistics:** expectancy، PF، max DD، Sharpe/Sortino، turnover، exposure، confidence intervals/bootstrap/Monte Carlo؛ PBO/DSR عند ملاءمتهما.
8. **Forward paper:** التوقع يسجل قبل النتيجة؛ يعاد ربطه بسعر/وقت/data hash فعلي.
9. **Promotion:** يمر لجنة/قواعد جودة؛ الفشل أو drift يخفضه إلى مراقبة أو يوقفه.

لا توجد حدود رقمية "سحرية" عامة لـPF أو Sharpe. تُحدد عتبات قبول مسبقاً بحسب الأداة، horizon، turnover والتكاليف، مع مقارنة إلزامية ضد baseline بسيط و`NO_TRADE`/cash.

## 7) ما تم بناؤه فعلاً في الإصدار الأول

- قاعدة معرفة JSON ذات مصدر وإصدار وتاريخ وعلاقات: `knowledge/`.
- objects صارمة للشموع وlineage وevidence/engine report/decision/signal/risk.
- validator يرفض future/unclosed/duplicate/out-of-order bars.
- محركات مستقلة: `price_action`, `ict`, `smc`, `volume`, `quantitative` ومحرك عدم-توفر للـorder flow/news.
- عقل مركزي يزيل تكرار evidence correlation ويعيد `NO_TRADE` عند ضعف الدرجة أو قلة المجموعات المستقلة أو تعارض HTF.
- risk gate لا يستنتج حجم عقد XAU/USD، يمنع limits اليومية/الأسبوعية/DD/ارتباط الصفقات.
- metrics وwalk-forward splits وbootstrap، مع اختبارات وحدات محلية.

**غير منفذ عمداً:** موصل بيانات، تاريخ حقيقي، Volume/Market Profile، order-flow حقيقي، Wyckoff/fundamental/NLP/ML، simulator fills كامل، dashboard، paper broker، وlive execution. التفاصيل المعمارية وخطة التنفيذ في [`ARCHITECTURE_AR.md`](ARCHITECTURE_AR.md) و[`DATA_SOURCES_AR.md`](DATA_SOURCES_AR.md).
