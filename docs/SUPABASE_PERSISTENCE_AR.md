# التخزين الدائم في Supabase — عقد Paper Lifecycle V0.12

## النطاق المنفذ وحدوده

يوفر هذا الإصدار **مسار تخزين server-side اختياري** للحقائق الناتجة عن
`OrderLifecycleManager` في دورة `PAPER` فقط. لا يقرأ هذا الموصل سعراً أو بيانات
سوق، لا يفتح session لدى BingX أو أي وسيط، لا ينشئ position، ولا يرسل أمراً حقيقياً.
وجود الجداول أو نجاح migration ليس دليلاً على اتصال سوق أو تفويض حساب أو نتيجة تداول.

الموصل موجود في `trading_intelligence.persistence.SupabaseLifecycleStore`، ولا يُنشأ
تلقائياً. التطبيق الذي يشغّل lifecycle هو من يمرّره صراحةً كـ`durable_store`:

```python
store = SupabaseLifecycleStore.from_environment()
manager = OrderLifecycleManager(
    ExecutionMode.PAPER,
    policy=reviewed_policy,
    durable_store=store,
)
```

إذا لم يكتمل زوج إعدادات Supabase فـ`from_environment()` يرجع `None` عند غيابهما
معاً، أو يفشل startup عند وجود واحدة فقط. لا توجد قيمة افتراضية ولا اتصال صامت.

## ما الذي تحفظه قاعدة البيانات؟

المسار يطبق migration:

`supabase/migrations/20261118175911_trading_intelligence_durable_order_audit.sql`

وهو مطبق أيضاً على مشروع Supabase المتصل باسم
`trading_intelligence_durable_order_audit` (migration version
`20261118175911`). أنشأ ثلاث جداول معزولة باسم `ati_` حتى لا تتداخل مع جداول التطبيق
الموجودة مسبقاً:

| الجدول | الغرض |
|---|---|
| `ati_order_intents` | intent غير قابل للتغيير: client id وSHA-256 fingerprint وملخص signal/policy الآمن |
| `ati_order_events` | سجل lifecycle مرتب ومؤرخ، event واحد لكل `(client_order_id, sequence)` |
| `ati_order_states` | read model للحالة الأخيرة؛ يُحدث داخل transaction نفسه |

لا يُحفظ API key أو secret reference أو account balance أو raw market payload في هذه
الجداول. يحتفظ `intent` بمستويات الإشارة وقرار المخاطر لأنها حقائق audit لازمة لفهم
حالة Paper؛ لا يعني ذلك أنه أمر صالح للإرسال.

## invariants التي تفرضها الدالة الذرية

كل كتابة تمر عبر RPC واحد فقط:
`public.ati_record_order_event(...)`.

1. `client_order_id` يرتبط بـrequest fingerprint وintent ثابتين؛ إعادة استخدامه
   لنية مختلفة تفشل. كما أن fingerprint فريد، فلا يمكن إعادة تغليف intent مطابق في
   client ID ثانٍ لإنشاء أمر Paper مكرر.
2. أول event رقمه `1`، وكل event تالٍ يجب أن يكون متصلاً تماماً بالرقم السابق.
3. تكرار HTTP الغامض للـevent نفسه آمن فقط إذا كانت payload مطابقة byte-for-meaning
   (مقارنة `jsonb`)؛ يرجع `duplicate` ولا ينشئ صفاً جديداً.
4. intent row مقفل `FOR UPDATE` داخل transaction، لذلك لا تتجاوز عمليتان متوازيتان
   ترتيب الأحداث نفسه.
5. event الجديد وcurrent state يكتبان في transaction واحدة.
6. إذا أعاد Supabase HTTP غير 2xx أو response غير متوقع، يرمي الموصل
   `SupabasePersistenceError` **قبل** أن يحدّث `OrderLifecycleManager` حالته المحلية.
   لا تُفسر timeout/rejection كفشل الأمر أو نجاحه؛ أعد نفس التحول غير القابل للتغيير
   ثم راجع `ati_order_events` من backend موثوق.

لمنع ambiguity بين سجلين مستقلين، يرفض `OrderLifecycleManager` تمرير
`audit_ledger=ResearchLedger(...)` و`durable_store` معاً. اختر hash-chain المحلي
للبحث المعزول، أو سجل Supabase الذري للتشغيل server-side؛ لا تدّعِ اتساقاً موزعاً بينهما.

## العزل والصلاحيات

- RLS مفعّل على الجداول الثلاثة.
- لا توجد policy لـ`anon` أو`authenticated`، و`REVOKE` يمنعهما من الجداول.
- RPC لا يملك `PUBLIC`/`anon`/`authenticated` صلاحية execute؛ صلاحية التنفيذ مقتصرة
  على `service_role` (بالإضافة إلى role إدارة PostgreSQL).
- الدالة `SECURITY INVOKER`: لا يوجد bypass مخفي أو security-definer surface. يعمل
  bypass RLS فقط عندما يستدعيها trusted backend بالمفتاح الخدمي.
- صفحة dashboard static والـcontrol-plane GET-only لا تستورد هذا الموصل ولا تعرض
  endpoint أو secret locator أو database credentials.

تم التحقق على المشروع المتصل من تفعيل RLS، ومن عدم وجود policies browser-facing، ومن
وجود `EXECUTE` فقط لـ`service_role`/إدارة PostgreSQL. كما نُفّذ RPC في transaction
من دون commit: سجل أول event ثم أعاد نفس event بنتيجة `duplicate` ثم rollback، لذلك
لا توجد بيانات اختبار دائمة في audit.

## إعداد deployment الآمن

القيم التالية تخص process backend فقط، ولا توضع في JavaScript أو dashboard أو
`NEXT_PUBLIC_*`/`VITE_*` أو Git:

```dotenv
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY_REF=sm://env/SUPABASE_SERVICE_ROLE_KEY
SUPABASE_PERSISTENCE_TIMEOUT_SECONDS=5
```

- `SUPABASE_URL` URL HTTPS bare بصيغة `<project-ref>.supabase.co` فقط؛ لا يقبل
  الموصل `http` أو hostname آخر أو مساراً أو query أو credential مضمن.
- `SUPABASE_SERVICE_ROLE_KEY_REF` locator فقط. مع resolver الافتراضي يجب أن يكون
  `sm://env/UPPERCASE_NAME`.
- القيمة الحقيقية في `SUPABASE_SERVICE_ROLE_KEY` تُدار كـSecret في منصة backend ولا
  تُطلب أو تُنقل في الدردشة أو Git أو المتصفح.
- Vercel المتصل حالياً لا يحوي environment variables، وdeployment الحالي static؛ لذلك
  migration موجود لكن **لا يوجد backend منشور/مهيأ يستخدم هذا المسار حالياً**.
  لا تضف قيمة placeholder، ولا تعتبر `READY` دليلاً على binding.

الـtemplate الرسمي موجود في `config/runtime.env.example`.

## قبل Forward Paper أو Live

هذه طبقة audit فقط. يلزم على الأقل قبل أي Forward Paper متصل:

1. backend مصادق ومراقب ينفّذ lifecycle من جانب الخادم ويملك timeout/retry/reconciliation runbook.
2. RBAC للمشغّلين ومراجعة retention/backup/incident response، وread models لا تعرض
   service role ولا أكثر من الحاجة.
3. data/feed entitlement موثق وlineage/freshness مستقلان عن persistence.
4. اختبارات disconnect وreconciliation وemergency stop على venue الحقيقي ضمن بيئة
   محاكاة معتمدة.
5. موافقة بشرية زمنية مستقلة وحدود مخاطرة صلبة قبل أي انتقال لا يزال محجوباً في هذا repo.

BingX live dispatch يبقى محجوباً، وGemini يبقى advisory-only خارج مسار
risk/decision/execution.
