# التشغيل والصيانة والتدريب — عقد عمليات V0.12

## الصدق التشغيلي أولاً

هذا المستودع ما زال منصة بحث وPaper محكومة، وليس خدمة تداول حية. أدوات التشغيل هنا
لا تتصل بسوق أو broker ولا تحل secret ولا تبدأ worker ولا تغيّر Supabase. لذلك نتيجة
`PASS` في الفحص المحلي تعني **سلامة artefacts والعقود المحلية فقط**، ولا تعني:

- أن مصدر أسعار متصل أو حديث أو مرخّص؛
- أن حساب BingX/Gemini/Supabase مهيأ؛
- أن استراتيجية مربحة أو اجتازت backtest حقيقياً؛
- أن incident drill على venue أو live approval قد اكتمل.

أي حالة غير معروفة في مسار data/risk/execution تظل سبباً لـ`NO_TRADE` أو تجميد
الترقية، لا سبباً للتخمين.

## 1. أداة الصيانة المحلية: Platform Doctor

شغّل من checkout موثوق:

```bash
# من جذر checkout موثوق فقط:
PYTHONPATH=src python -m trading_intelligence.cli --doctor
# أو بعد تثبيت الحزمة، مع البقاء في جذر checkout نفسه:
trading-intelligence --doctor
```

الأداة تطبع JSON مناسباً لبرنامج CI أو runbook وتعيد non-zero عند failure. مع
`--strict` تعيد non-zero أيضاً عند warning. هي تفحص محلياً فقط:

| check | ما يثبته | ما لا يثبته |
|---|---|---|
| `knowledge_registry` | schema وsources/relations للـknowledge canonical سليمة | صحة أو ربحية أي مدرسة تحليل |
| `knowledge_bundle` | تطابق SHA-256 بين registry canonical والنسخة المضمنة في package | حداثة المصادر على الإنترنت |
| `version_alignment` | تطابق `pyproject.toml` وpackage version | نجاح نشر أو build خارجي |
| `runtime_template` | لا assignment مباشر لمفتاح حقيقي في template؛ locators فقط | وجود secret حقيقي أو صحته |
| `supabase_migration` | وجود safeguards المطلوبة في SQL migration المراجعة | اتصال REST/Supabase فعلي أو صلاحيات runtime |
| `dashboard_boundary` | headers الأساسية وعدم وجود أسماء secrets في JavaScript | identity/RBAC أو API backend منشور |
| `research_ledger` | hash-chain لملف محدد عند تمريره | اكتمال جميع أحداث النظام |

لفحص Ledger اختياري:

```bash
PYTHONPATH=src python -m trading_intelligence.cli --doctor \
  --ledger /trusted/audit/research.jsonl --strict
```

لا تمرّر مساراً network-shared غير موثوق، ولا تضع credentials داخل argument أو ملف
ledger. عند فشل check، احفظ JSON الناتج كدليل incident ثم أصلح السبب وأعد الفحص؛ لا
تتجاوز الفشل بتغيير status يدوياً.

## 2. مراقبة feeds والحوادث

`OperationalMonitor` يبقى model محلياً حتى يربطه backend مستقل بسجل دائم وalert
transport. التحسينات الحالية تمنع green status مضللاً:

1. `expected_feeds=(FeedKey(...), ...)` يجعل غياب heartbeat لأي feed مطلوب
   `CRITICAL` بدلاً من اعتباره healthy.
2. heartbeat بتاريخ لاحق لـ`as_of` هو clock/lineage anomaly و`CRITICAL`، لا "fresh".
3. heartbeat قديم أو incident مفتوح حرج يبقى `CRITICAL`.
4. incident من نوع `DEGRADED` يبقى مرئياً حتى resolve timestamp صحيح.

لا يكفي إنشاء monitor فارغ للادعاء أن feed صحي؛ في وضع `ANALYSIS_ONLY` لا توجد feeds
متوقعة افتراضياً. عند تفعيل Forward Paper يجب تمرير allowlist صريحة لكل
`provider/instrument/timeframe` إلى monitor وربط `CRITICAL` بالحظر قبل signal/order
lifecycle في backend، مع alert delivery خارج هذا core.

## 3. إيقاع الصيانة المقترح

| التوقيت | المسؤول | الإجراء القابل للتدقيق | قرار آمن عند failure |
|---|---|---|---|
| كل تغيير برمجي/نشر | maintainer | `--doctor` + unit suite + package install smoke | لا تنشر/لا تروّج النسخة |
| يومي في Forward Paper | on-call | freshness/expected feeds، incidents، clock skew، ledger hash | `NO_TRADE`، افتح incident، لا تعيد تشغيل عشوائياً |
| قبل تغيير data/strategy/model | reviewer مستقل | source/version/dataset hash، ablation/OOS plan، rollback plan | يظل التغيير draft |
| أسبوعي | risk owner | exposure/DD/limit review، pending `UNKNOWN` reconciliation | freeze promotion، لا تضاعف الحجم |
| شهري أو بعد incident | security + operator | secret rotation/least privilege/RBAC/retention review | عطّل binding المشكوك فيه |

يجب تخزين logs وreports خارج Git وبسياسة retention/least privilege مناسبة. لا تسجل
API keys أو service role أو order raw payload حساس في dashboard، PR، أو alert text.

## 4. برنامج التدريب والـdrills

هذه drills مطلوبة للتدريب ولا تُعد "passed" من وجود هذا الجدول. لكل drill سجّل:
التاريخ UTC، البيئة، operator/reviewer، version/hash، البيانات المدخلة، النتيجة،
الملاحظات، corrective action، وموافقة مستقلة.

| Drill | بيئة آمنة | نجاح قابل للقياس | يمنع الادعاء |
|---|---|---|---|
| Knowledge tamper | نسخة اختبار من JSON/ledger | `doctor` أو `ResearchLedger.verify()` يكتشف التعديل | أن integrity يكفي لإثبات جودة الاستراتيجية |
| Missing/stale feed | fixture أو provider sandbox | expected feed يصبح `CRITICAL` ويمتنع النظام عن Paper candidate | ملء gap أو reuse لسعر قديم |
| Future timestamp / clock skew | fixture heartbeat لاحق | monitor يصدر `CRITICAL` | اعتبار timestamp المستقبلي fresh أو استخدامه في analysis |
| Paper idempotency | mock/VST فقط | retry مطابق لا يخلق lifecycle event/order ثانياً | أن VST test يجيز live dispatch |
| Disconnect/reconciliation | sandbox/mock venue snapshot | in-flight يصبح `UNKNOWN` ولا يتحول terminal بلا snapshot موثق | افتراض cancel/fill عند timeout |
| Emergency stop | Paper sandbox | intents الجديدة محجوبة، known states تتبع contract، UNKNOWN يبقى reconciliation-required | ادعاء إغلاق مركز حقيقي بلا venue confirmation |
| Secret exposure | CI/review fixture | doctor/template/browser checks تفشل عند raw secret | أن locator هو secret value أو أن Vercel static backend آمن تلقائياً |
| Supabase audit retry | migration project غير إنتاجي أو transaction rollback | نفس event يرجع `duplicate`؛ event مختلف يفشل | أن RPC حلّ reconciliation مع venue |

قبل أي Live study يجب تنفيذ drills على بيئة منفصلة وببيانات evidence خارجية، إضافةً إلى
Forward Paper المتصل، reconciliation للأوامر والمراكز، limits صلبة، وموافقة بشرية
محددة المدة. لا يوجد live sender في هذا repo.

## 5. الاستجابة للخطأ والتحسين المنضبط

1. **اكتشف:** التقط doctor/monitor/ledger output ووقت UTC وversion/hash. لا تمسح
   artefacts أو تعيد كتابة ledger.
2. **احتوِ:** عند data/risk/clock/execution uncertainty، فعّل `NO_TRADE` أو
   emergency stop حسب العقد. لا تستخدم martingale أو retry write غير idempotent.
3. **صنّف:** data quality، secret/configuration، code regression، venue ambiguity،
   أو research validity. لا تخلط سبب السوق بسبب النظام.
4. **أصلح في branch مراجع:** test reproducing bug أولاً، ثم code/migration/runbook
   ثم full suite وdoctor. أي schema change يحتاج migration قابلة للمراجعة.
5. **تحقق مستقل:** أعد drill المناسب، واربط evidence بالـincident. لا تغلق incident
   لأن process أعيد تشغيله فقط.
6. **تعلم بحذر:** اقتراحات AI/Gemini قد تلخص incident، لكنها لا تغيّر risk limits أو
   execution state أو قرار promotion.

## 6. حدود الخدمات المتصلة حالياً

- Supabase migration/RLS/RPC مطبقة، لكن لا يوجد binding backend منشور أو REST call
  بتفويض التطبيق ضمن هذا الإصدار.
- Vercel الحالي يستضيف static dashboard؛ لا يوضع فيه service-role أو broker secret.
- BingX live dispatch محجوب صراحةً؛ VST/public data لا يساويان entitlement أو Live.
- Gemini اختياري وadvisory-only؛ لا يدخل decision/risk/execution.

تفاصيل الأسرار في [`EXTERNAL_INTEGRATIONS_AR.md`](EXTERNAL_INTEGRATIONS_AR.md)،
وسجل Paper الدائم في [`SUPABASE_PERSISTENCE_AR.md`](SUPABASE_PERSISTENCE_AR.md)،
وسلامة التنفيذ في [`EXECUTION_SAFETY_AR.md`](EXECUTION_SAFETY_AR.md).
