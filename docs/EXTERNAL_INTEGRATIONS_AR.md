# تكامل BingX وGemini والأسرار — عقد التشغيل V0.11

**تاريخ مراجعة المصادر:** 2026-11-18 UTC. هذا العقد يفرق بين ما هو منفذ في الكود، وما يحتاج binding server-side، وما يبقى محجوباً قبل موافقة بشرية.

## 1. المصادر الرسمية المثبتة

### BingX

- [BingX API Docs الرسمية](https://github.com/BingX-API/docs): تربط مراجع Spot وPerpetual Swap وStandard Contract وAccount/Wallet.
- [BingX OpenAPI Skills الرسمية](https://github.com/BingX-API/api-ai-skills) عند commit [`5d96e72561c97de2ec3ba8650e48582c371cd4ad`](https://github.com/BingX-API/api-ai-skills/commit/5d96e72561c97de2ec3ba8650e48582c371cd4ad): مصدر مراجعة adapter الحالي.
- العقد المستخدم في الكود: USDT-M Perpetual public klines `GET /openApi/swap/v3/quote/klines`، وVST test order `POST /openApi/swap/v2/trade/order/test`، وHMAC-SHA256 للـprivate parameters، مع `timestamp` بالمللي ثانية و`recvWindow <= 5000` ورأسي `X-BX-APIKEY` و`X-SOURCE-KEY: BX-AI-SKILL`.
- البيئات الرسمية المراجعة: `prod-vst` عبر `https://open-api-vst.bingx.com` (والـ`.pro` fallback لقراءة GET الشبكية فقط)، و`prod-live` عبر `https://open-api.bingx.com`. لا يوجد fallback لعمليات الكتابة.

### Gemini

- [Google Gen AI Python SDK الرسمي](https://github.com/googleapis/python-genai) عند commit [`acf76e4a284273082e487af7ff77a3949d9ad797`](https://github.com/googleapis/python-genai/commit/acf76e4a284273082e487af7ff77a3949d9ad797).
- التكامل اختياري عبر `pip install 'trading-intelligence[gemini]'`. SDK لا يُحمّل إلا داخل backend عند وجود config ومفتاح server-side.
- Gemini هنا **ADVISORY_ONLY**: يقرأ brief محدوداً من شموع OHLCV المغلقة وتقارير المحركات بعد القرار، ويعيد شرحاً/عدم يقين فقط. لا يعيد كتابة decision أو risk أو signal أو execution.

## 2. ما هو منفذ فعلياً الآن

| القدرة | التنفيذ | الحد الآمن |
|---|---|---|
| BingX OHLCV | `BingXSwapMarketDataProvider` يطلب public USDT-M klines ويهمل الشمعة غير المغلقة ويسجل SHA-256/lineage | caller يجب أن يربط symbol map صريحاً ويمرر البيانات إلى quality pipeline؛ لا ادعاء اتصال قبل استدعاء حقيقي ناجح |
| BingX private signing | `BingXSwapTestOrderClient` يبني canonical ASCII-sorted parameters ويوقع HMAC-SHA256 server-side | يدعم **VST test order فقط**؛ لا fallback ولا retry للكتابة |
| BingX real order | غير ممكّن | `submit_live_order()` يرفض صراحة. نجاح `/order/test` ليس إثباتاً لقبول live order |
| Gemini chart adviser | `GeminiChartAnalyst` يرسل completed-candle brief إلى SDK الرسمي بعد حل secret في backend | output advisory فقط، وبلا صور TradingView أو حساب أو مفاتيح أو سلطة تنفيذ |
| Chart agents | `ChartAnalysisAgent` يشغل أولاً المحركات/central/risk ثم يرفق Gemini advisory اختياري | Gemini لا يدخل كدليل في central scoring ولا يغير `PAPER_CANDIDATE` |
| Supabase Paper audit | `SupabaseLifecycleStore` يستدعي RPC ذرياً لتثبيت intent/event/state مع RLS | server-side service role فقط؛ لا browser/db connection ولا order/market route؛ راجع `SUPABASE_PERSISTENCE_AR.md` |

## 3. قائمة المفاتيح الحقيقية

**ضع القيم داخل Secret Manager أو encrypted server runtime فقط. لا تلصقها هنا ولا في Git أو dashboard أو `NEXT_PUBLIC_*`.**

| سجل Secret Manager | القيمة الحقيقية | الاستخدام | الحالة الموصى بها |
|---|---|---|---|
| `BINGX_API_KEY` | API Key من BingX User Center → API Management | authenticated BingX requests | ابدأ read-only وIP allowlist |
| `BINGX_SECRET_KEY` | Secret Key المقابل | HMAC-SHA256 server-side فقط | لا يظهر في logs أو API response |
| `GEMINI_API_KEY` | Gemini Developer API key | Gemini advisory server-side فقط | model/advisory فقط، لا execution |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role secret للمشروع المحدد | استدعاء RPC `ati_record_order_event` من backend فقط | لا يخرج للمتصفح؛ لا تستخدمه إلا مع RLS/RPC المحدودين في `SUPABASE_PERSISTENCE_AR.md` |

**إعداد BingX للمفتاح:** فعّل IP allowlist، امنع withdrawals دائماً، ابدأ بلا write permission أو بحساب/بيئة VST، ولا تمنح صلاحيات نقل أموال/sub-accounts/wallet/copy-trading.

## 4. متغيرات Backend غير العامة

هذه قيم config أو **مراجع** وليست secrets نفسها:

```dotenv
# Secret references: server process only
BINGX_API_KEY_REF=sm://env/BINGX_API_KEY
BINGX_API_SECRET_REF=sm://env/BINGX_SECRET_KEY
# أضف هذا المرجع فقط مع GEMINI_ROLE=ADVISORY_ONLY
# GEMINI_API_KEY_REF=sm://env/GEMINI_API_KEY

# Optional: durable Paper lifecycle audit, backend only
# SUPABASE_URL=https://your-project-ref.supabase.co
# SUPABASE_SERVICE_ROLE_KEY_REF=sm://env/SUPABASE_SERVICE_ROLE_KEY
# SUPABASE_PERSISTENCE_TIMEOUT_SECONDS=5

# Start in simulated environment
BINGX_ENVIRONMENT=prod-vst
BINGX_READ_ONLY_VERIFIED=false

# Enable Gemini strictly as a chart adviser after server-side secret binding
GEMINI_ROLE=ADVISORY_ONLY
GEMINI_MODEL_ID=YOUR_REVIEWED_GEMINI_MODEL_ID
```

`EnvironmentSecretResolver` يقبل فقط الشكل `sm://env/UPPERCASE_NAME`. أما `vault://...` و`secret://...` فتحتاج resolver خاصاً لمزود vault؛ لا تتحول تلقائياً إلى environment lookup.

`BINGX_VENUE_CONTRACT_JSON` يبقى مطلوباً لـcontrol plane عند binding مفاتيح BingX؛ يجب أن يحتوي contract review موثقاً، وليس endpoint أو capability مخمنين. يوجد قالب خالٍ من الأسرار في [`config/runtime.env.example`](../config/runtime.env.example).

## 5. ترتيب التفعيل الإلزامي

1. أضف سجلات Secret Manager المطلوبة **للتكاملات التي ستفعلها فقط**، ثم أضف **المراجع فقط** إلى backend runtime. لا تفعل Gemini أو Supabase لمجرد وجود secret.
2. عند احتياج Paper audit دائم، طبّق migration Supabase المراجَع واربط `SUPABASE_SERVICE_ROLE_KEY_REF` داخل backend فقط؛ تحقق من RLS/RPC قبل تسجيل أول lifecycle event. لا تنشره في Vercel static أو المتصفح.
3. شغّل BingX public kline adapter على `prod-vst` أو source read-only ومرر النتيجة عبر gaps/duplicates/lineage/freshness pipeline.
4. اربط `BingXSwapTestOrderClient` في `prod-vst` فقط؛ نفذ `/order/test` مع client order ID فريد وسجل response/reconciliation drill. لا يعني النجاح أن live valid.
5. فعّل Gemini `ADVISORY_ONLY` بعد تثبيت model id؛ راجع output كملاحظة منفصلة لا كقرار.
6. وثق Forward Paper وdisconnect/reconnect/cancel/reconciliation/emergency-stop drills.
7. أضف RBAC/persistent approvals وموافقة بشرية محددة المدة/الأداة/limits.
8. **فقط في تغيير مستقل مراجع**: live order adapter، مع human approval لكل intent، idempotency، pre-trade validation، post-trade reconciliation، kill switch، ومراقبة دائمة. الإصدار الحالي لا يقوم بهذه الخطوة.

## 6. حدود مهمة

- Vercel static preview لا يشغل Python control plane أو providers. يلزم deployment backend/HTTPS reverse proxy للـWSGI وخدمات worker/polling server-side.
- بيانات BingX هي Crypto perpetual وليست بديلاً عن XAU/USD أو GC أو volume profile/footprint/L2.
- لا توجد ربحية مضمونة؛ Gemini لا يقدم probability أو توصية تنفيذ.
- لا يجوز إدخال المفاتيح من UI أو `localStorage` أو URL أو logs أو PR comments.
