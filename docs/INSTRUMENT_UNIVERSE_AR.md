# نطاق الأدوات المدعوم وهوية المنتج

**الحالة الحالية:** يدعم الكود هوية وتحليل الأدوات التالية عبر `InstrumentRegistry` في
`src/trading_intelligence/providers/instruments.py`. هذا **لا** يعني أن مزود بيانات حي،
وسيط، أو مواصفة تنفيذ لأي أداة مهيأ في هذا المستودع.

## الهوية القانونية للبحث

| canonical id | فئة الأصل | `instrument_kind` | السعر الافتراضي | شرط لا يمكن تجاوزه |
|---|---|---|---|---|
| `XAUUSD` | معدن spot | `XAUUSD_SPOT` | `MID` | quote مزود/broker محدد؛ لا يستبدل بـGC |
| `EURUSD` | FX spot | `FX_SPOT` | `MID` | وثّق BID/ASK/MID وجلسات/عطل FX |
| `US500` | مؤشر cash | `INDEX_CASH` | `LAST` | cash index وCFD وETF وfuture منتجات مختلفة |
| `AAPL` | سهم | `EQUITY` | `LAST` | corporate actions وexchange/RTH/ETH calendar |
| `BTCUSD` | crypto spot | `CRYPTO_SPOT` | `LAST` | venue/quote currency/24×7 outage policy |
| `GC` | future | `GC_FUTURE` | `LAST` | عقد شهر/سنة أو roll policy موثق قبل التحليل |

السجل يمنع خلط المعنى التشغيلي بين الأسماء، لكنه لا يمنح `InstrumentRiskSpec` أو
mapping لمزود أو entitlement. تلك الثلاثة منفصلة عمداً:

```text
canonical research instrument != vendor symbol != execution product specification
```

## متطلبات اتصال مصدر جديد

1. يمرر caller mapping صريحاً من canonical ID إلى vendor symbol في adapter معتمد.
2. يثبت `instrument_kind` و`price_component` المطابقين للسجل؛ لا تخلط `MID` و`LAST` في snapshot واحد.
3. يسجل UTC، `retrieved_at`، raw request/payload hash، vendor symbol، mode، وحالة الجودة.
4. يحدد سياسة session/holiday/DST حيث يطلبها السجل؛ لا يجوز لـD1/W1/MN أو profile افتراض جلسة.
5. يضيف fixture لا يحتوي فقط على happy path بل gap وduplicate وrevision وbar غير مكتمل وstale feed.
6. قبل Paper أو Forward Paper، يربط مواصفات المنتج الصريحة: multiplier وminimum/step وcurrency وحدود المخاطر. لا تستنتج هذه من ticker.

## حالة الواجهات الحالية

- `CSVBarSpec` يستورد history يقدمه المستخدم/مزود مرخّص لأية أداة بشرط تحديد identity وtimezone وprice component.
- `MarketDataProvider`/`MarketDataPipeline` يدعمان provider adapter read-only للأداة المناسبة، poll صريح، lineage، gap/anomaly/replay/revision detection وsnapshot quality-gated.
- `OandaV20MarketDataProvider` مثال adapter قابل للحقن لـOHLC فقط ضمن mapping صريح؛ لا يوجد token أو اتصال فعلي أو إثبات entitlement.
- لا يوجد adapter exchange/equity/crypto/indices مهيأ حالياً، ولا يحق للرسم الخارجي TradingView أن يصبح مصدراً لمحركات البحث.

## حدود التحليل حسب الفئة

كل محرك يتعامل مع `MarketSnapshot` المغلق والموثق، لذلك يمكن تشغيل قواعد OHLC/volume حيث تتوفر دلالات بيانات صحيحة. أما Order Flow وFootprint وDelta/CVD وVolume/Market Profile فتظل مرفوضة صراحة إلى أن تصل tick/L2/VAP من **venue مناسب**. لا يحوّل حجم candle أو رسم خارجي إلى microstructure.

المراجع الخاصة بـGC وOANDA موجودة في [`knowledge/sources.json`](../knowledge/sources.json). وهي توثق هوية/API في نطاقها فقط، وليست تصريحاً بأن مصدرًا أو أداةً أو حساباً متاح أو مرخص للمستخدم.
