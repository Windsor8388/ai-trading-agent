# خريطة مدارس التداول إلى محركات قابلة للمراجعة

## مبدأ الخريطة

`src/trading_intelligence/engines/catalog.py` هو سجل versioned يربط كل محرك بـ:

- المدرسة والمفاهيم (`concept_ids`) في `knowledge/concepts.json`.
- المدخلات الدنيا التي لا يجوز اختراعها.
- حالة القدرة: `IMPLEMENTED` أو `DATA_REQUIRED` أو `TEMPLATE_REQUIRED` أو `RESEARCH_BACKLOG`.
- حد واضح يمنع تحويل مصطلح مدرسي إلى ادعاء alpha أو تنفيذ.

الاختبار يتحقق أن كل concept في catalog موجود في `KnowledgeBase` وصالح المصدر/العلاقات. لا تعني `IMPLEMENTED` نجاحاً إحصائياً؛ تعني أن هناك rule ميكانيكية وfixture محلية فقط.

| المدرسة/المحرك | ما ينفذ الآن | المدخلات الدنيا | الحد العلمي/التشغيلي |
|---|---|---|---|
| ICT | FVG وequal-high/low sweep | OHLC مغلق، ATR، pivots مؤكدة | heuristic؛ لا order book ولا claim proprietary |
| SMC / Price Action | structure، OB proxy، BOS/CHoCH/MSS candidate | OHLC مغلق، ATR، pivots | الأدلة تشترك في `market_structure` ولا تعد مستقلة تلقائياً |
| Wyckoff | spring/upthrust حول range trailing | OHLC، وحجم موثق لتحسين confidence | لا phase accumulation/distribution معلنة كحقيقة |
| Supply/Demand | last-opposite-candle قبل displacement | OHLC، ATR | zone proxy وليس volume/order-book demand |
| S/R | clustering لـconfirmed pivots | OHLC، pivots، ATR | لا رسم بصري أو مستوى غير قابل لإعادة الإنتاج |
| Fibonacci | قرب retracement لنطاق ratios ثابت | opposite confirmed pivots، ATR | feature hypothesis؛ ratio ليس سبباً مثبتاً |
| Liquidity | equal-high/low sweep proxy مستقل | OHLC مغلق، ATR؛ L2 للقياس الحقيقي | لا يزعم رؤية resting liquidity؛ يبقى ضمن correlation group structure |
| Quant | SMA20/50 + volatility gate | OHLC مغلق | baseline، وليس ML أو claim alpha |
| Volume | relative volume z-score | OHLCV ودلالة حجم موثقة | لا Profile/CVD من volume الشمعة |
| Fundamental | event-risk window | calendar ممهور زمنيًا | لا sentiment أو اتجاه خبر بلا feed مرخص |
| Sessions | label UTC عام | timestamp UTC | ليس exchange calendar أو DST policy |
| Volume Profile | report missing input | VAP/trade prints | `DATA_REQUIRED`؛ لا POC/value area مصطنع |
| Market Profile | report missing input | session-aware TPO/VAP | `DATA_REQUIRED` |
| Order Flow | report missing input | trades + quotes/L2 | `DATA_REQUIRED` لـdelta/CVD/footprint/imbalance |
| Classical patterns | report missing template | geometric template + breakout/cost policy | `TEMPLATE_REQUIRED` لمنع post-hoc labels |
| ML/DL/RL/NLP | governance فقط | point-in-time features + OOS + baseline | `RESEARCH_BACKLOG`؛ لا model مدرب |

## تشغيل محركات المجموعة

```python
from trading_intelligence.engines import default_research_engines, engine_catalog
from trading_intelligence.orchestration import AnalysisOrchestrator

catalog = engine_catalog()  # capability/status documentation for UI or API
orchestrator = AnalysisOrchestrator(default_research_engines())
result = orchestrator.run(snapshot, ("D1", "H4", "H1", "M15"))
```

للتجارب الورقية/forward التي تحتاج سجلاً، استخدم `orchestrator.run_and_record(..., ledger=ResearchLedger(...), run_id="...")`. يسجل كل `EngineReport`، بما في ذلك reports ذات المدخلات الناقصة، في JSONL hash-chain حتى لا تختفي نتيجة `NO_RESULT` من المراجعة.

هذا لا يتصل بسوق ولا يضمن أن كل محرك يملك inputsه. المحركات ذات البيانات الناقصة تصدر `EngineReport.required_inputs_missing` أو report غير اتجاهي؛ لا ينبغي استبدالها بنتيجة افتراضية.

## ترتيب البحث قبل الترقية

1. جمّد rule/version/parameters لكل concept.
2. اختبر engine منفرداً على fixture ثم historical data ذات lineage.
3. اختبر ablation: هل يضيف المحرك قيمة فوق baseline وبعد de-correlation؟
4. استخدم walk-forward وOOS مقفل؛ لا تعيد ضبط parameters على final holdout.
5. لا ينقل concept إلى Paper candidate إلا مع data contract وrisk approval منفصلين.
