# طبقة مزود البيانات وخط أنابيب الجودة

## الحقيقة التشغيلية الحالية

لا يوجد token أو حساب OANDA أو مصدر أسعار حي مضبوط في هذا الفرع، ولم يُرسل أي طلب API حقيقي ولم تُحلل أسعار سوق حديثة. الموجود هو كود read-only قابل للتشغيل بعد موافقة صريحة وإعداد runtime آمن، واختباراته تستخدم fixtures محلية فقط.

## ما بُني

```text
providers/contracts.py  → العقد المجرد للمصدر/الطلب/الاستجابة
providers/oanda_v20.py  → تحويل candles OANDA v20 عند حقن transport + token read-only
providers/pipeline.py   → quality gate، lineage receipt، gap/anomaly classification، polling plan
monitoring.py            → heartbeat/incident state محلي منفصل
```

### OANDA v20 adapter

`OandaV20MarketDataProvider` لا يقرأ `ENV` ولا ينشئ connection عند import أو construction. يتطلب جميع العناصر التالية من runtime مفوّض:

1. `OandaV20Config(access_token=...)` read-only من secret manager.
2. canonical-to-vendor mapping، مثل `XAUUSD → XAU_USD`، مع `instrument_kind` صريح.
3. transport محقون؛ يمكن أن يكون `UrllibJsonTransport` فقط بعد قرار التطبيق الصريح باستدعاء `fetch_completed_bars()`.
4. `MID` أو `BID` أو `ASK` محددة مرة واحدة في config والطلب.

ينفذ فقط M1/M5/M15/M30/H1/H4. لا يقبل D1/W1/MN لأن daily alignment والجلسة/DST للمزود لم تُعتمد. يستبعد `complete=false` ويخزن raw SHA-256، request ID، vendor symbol، URL بلا query/token، وعدد bars المستبعدة.

> لا تعني إمكانية تشغيل adapter أن XAU/USD متاح للحساب أو المنطقة أو أن الترخيص يسمح بالتخزين/إعادة النشر. يجب التحقق من entitlement وpricing segment وحقوق الاستخدام أولاً.

## بوابة الجودة والـlineage

`MarketDataPipeline.ingest()` لا يملأ أو يصلح البيانات. يسجل `IngestionReceipt` لكل محاولة، ويقبل output فقط إذا:

- `provider.name` وinstrument وprice component في الاستجابة تطابق الطلب.
- جميع الشموع مغلقة ومرتبة وبدون duplicate/future bar عند `observed_at`.
- gap count لا يتجاوز `DataPipelinePolicy.max_missing_bars`.
- anomaly policy لا تطلب الحجر عند وجود range-outlier.

الـgap يبقى gap ويظهر بعدد bars المفقودة. range-outlier يقارن range الحالي بوسيط آخر 20 ranges؛ هو **علامة مراجعة** لا دليل سعر خاطئ ولا auto-correction. policies لا تغني عن calendar/holiday/session policy خاصة بالأداة.

`latest_accepted()` لا يعيد إلا receipt مقبولاً، بينما receipts المرفوضة محفوظة محلياً لتوضيح سبب الحجر.

## التحديث الدوري

`PeriodicPollingCoordinator` ليس scheduler thread ولا خدمة دائمة. يسجل schedule بـcadence و`request_factory(now)`، ولا ينفذ أي fetch إلا عندما يستدعي process خارجي `poll_due(now)`. هذه الحدود مقصودة:

- يمكن اختباره deterministically.
- لا يحصل اتصال سوق مفاجئ عند تشغيل المكتبة.
- يمكن أن يضيف runtime لاحق health check، rate limit، retry/backoff، locks، persistence، وalert transport خارج core domain.

## الانتقال إلى مصدر فعلي

قبل وصل مصدر حقيقي:

1. موافقة بشرية على المصدر والحساب read-only والنطاق.
2. secret manager وleast privilege؛ لا client-side dashboard ولا Git ولا logs تحتوي token.
3. fixture من response حقيقي **منقح** يتضمن incomplete/gap/duplicate/schema error ومقارنة counts مع vendor.
4. سياسة session/holiday/DST وbar-close watermark لكل instrument/timeframe.
5. persistent raw storage وimmutable lineage/audit؛ الذاكرة المحلية الحالية ليست storage إنتاجي.
6. `OperationalMonitor` heartbeat وincident integration؛ stale feed يجب أن يبقي Trade Signal في `NO_TRADE`.

لا يفتح هذا المستند أو الكود تفويضاً للتداول أو لإنشاء broker order.
