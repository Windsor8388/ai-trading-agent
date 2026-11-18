# بوابات التكامل الخارجي: BingX وGemini

## قرار التشغيل المسجل

اختيار المستخدم الحالي هو نطاق BingX **حقيقي كامل** مع إدارة الأسرار عبر **secret manager server-side**، ودور Gemini هو **معطّل**. هذا لا يضع مفتاحاً في المستودع أو المتصفح ولا يحوّل المنصة تلقائياً إلى تداول حي؛ مفتاح الوصول وحده ليس موافقة تشغيل أو دليلاً على صحة عقد API.

## ما ينفذه الكود الآن

`src/trading_intelligence/integrations.py` يضيف عقود runtime آمنة:

- `SecretReference` يقبل locator مثل `vault://trading/bingx/api-key` أو `secret://...` أو `sm://...` فقط. يرفض قيمة API key أو token داخل المرجع.
- `BingXVenueContract` يفرض URL HTTPS موثقاً، إصدار documentation/API، account scope، ودعماً معلناً لـclient-order-id وcancel وorder-status/position reconciliation وstream recovery.
- `BingXRuntimeBinding` يجمع **مراجع** المفتاح/السر وعقد venue المراجع؛ لا يحتوي قيمة سر.
- `LiveOperationApproval` موافقة بشرية محددة الزمن والنطاق، تشمل الأدوات المسموحة وحد order notional والخسارة اليومية ونجاح forward-paper/reconciliation/incident drills.
- `LiveIntegrationGate.bingx_readiness()` يعرض حالة قابلة للتدقيق من `AWAITING_SECRET_BINDING` إلى `FORWARD_PAPER_READY` أو `LIVE_BLOCKED`، ولا يقرأ secret ولا يرسل طلب HTTP أو order.
- `RuntimeControlPlane` في `control_plane.py` هو backend WSGI صغير ومنفصل عن `dashboard/`: يتيح فقط `GET /healthz` و`GET /api/v1/control-plane/status` عبر origin نفسه. response لا يحتوي secret reference أو API URL أو account ID أو قيمة مفتاح، و`trade_dispatch=UNAVAILABLE` ثابت؛ لا توجد أي route للأوامر أو الحساب أو تغيير policy.
- dashboard يمكنه عند ضغط المستخدم طلب endpoint الحالة النسبي فقط (`credentials: omit`, `no-store`) ثم يقبل فقط `ANALYSIS_ONLY` و`UNAVAILABLE`. فشل الـendpoint يعيد الواجهة إلى حالة disconnected ولا يدعي اتصالاً. نشر Vercel static وحده لا يوفر هذا الـbackend؛ يلزم reverse proxy/server deployment للـWSGI بنفس origin.
- `GeminiIntegrationPolicy` مضبوط `DISABLED`. إن فُعل لاحقاً، دوره advisory/explanation فقط ولا يجوز له تغيير قرار المحركات أو risk أو execution.

لتشغيل endpoint الحالة محلياً فقط (من دون market/account/order endpoint):

```bash
PYTHONPATH=src python -m trading_intelligence.control_plane
# أو بعد تثبيت الحزمة: trading-intelligence-control-plane
```

الخادم يربط افتراضياً إلى `0.0.0.0:8080` ويدعم health/status فقط. عند النشر الفعلي يجب وضعه خلف HTTPS reverse proxy وإتاحة مسار الحالة نفسه من origin الواجهة؛ لا تضع أي binding أو secret في JavaScript أو `vercel.json`.

## لماذا لا يوجد BingX order sender الآن؟

لا توجد في هذه الجلسة وثائق API رسمية مثبتة الإصدار، account scope، رموز/عقود، أو endpoint/signing/reconciliation policy معتمدة لـBingX. لا يجوز تخمين هذه العناصر في كود مالي أو استعمال رسم TradingView كبديل عنها. لذلك لا يضم المستودع adapter يتصل فعلياً بـBingX بعد؛ هو يتطلب مراجعة مستقلة قبل أن يرى أي runtime secret.

المسار المطلوب قبل كتابة adapter الحقيقي:

1. تثبيت رابط documentation الرسمي وإصدار API وSPOT أو FUTURES محدد في `BingXVenueContract`.
2. secret manager يحقن key/secret **server-side فقط**؛ لا chat، لا Git، لا `NEXT_PUBLIC`/Vercel browser env، ولا logs.
3. البدء بـread-only market/account endpoint وfixture منقح من response حقيقي، ثم فحص identity/timezone/complete bars/lineage.
4. Forward Paper إلزامي: order intent محلي، reconcile order/position، cancel/reject/disconnect drills، monitoring وincident log.
5. توقيع `LiveOperationApproval` مستقل ومحدد المدة/الأدوات/limits بعد اجتياز drills.
6. adapter order منفصل ومراجع، يدعم idempotency بـclient order id، query status، cancel، reconnect، position reconciliation، emergency-stop ولا يتجاوز `RiskManagementEngine` أو `ExecutionPolicy`.
7. في هذا الإصدار تبقى الحالة `LIVE_BLOCKED` حتى بعد اكتمال السجلات البشرية، لأن adapter غير موجود. لا يجوز إنشاء `LIVE_GATE_READY` أو إرسال فعلي قبل مراجعة adapter وتفعيل deployment controlled منفصل.

## وكيل التحليل

`src/trading_intelligence/agents.py` يحتوي `MarketResearchAgent`، وهو agent منضبط وليس LLM. ينسق:

```text
accepted MarketSnapshot
  -> independent engines
  -> de-correlated central decision
  -> reference level planner (closed-bar ATR policy)
  -> independent risk gate
  -> PAPER_CANDIDATE or BLOCKED/NO_TRADE
```

`ReferenceLevelPlanner` ينتج entry/stop/target **مرجعية بحثية** فقط من close وATR مغلقين وبـRR معلن؛ ليست quote أو fill أو أمر. لا يتجاوز هذا الوكيل `TradeSignalEngine`، ويبقى وضع `LIVE` محجوباً في المستودع.
