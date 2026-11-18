# بوابات التكامل الخارجي والتداول الحقيقي

تفاصيل BingX/Gemini والمفاتيح ومراجعة API موجودة في [EXTERNAL_INTEGRATIONS_AR.md](EXTERNAL_INTEGRATIONS_AR.md). هذه الوثيقة تركز على **الحاجز التشغيلي**: وجود key أو adapter لا يعني السماح بالتداول الحقيقي.

## حالات التكامل

- `DISABLED`: integration غير مفعل. Gemini الافتراضي كذلك إلى أن يوجد `GEMINI_ROLE=ADVISORY_ONLY` ومرجع server-side وmodel id.
- `AWAITING_SECRET_BINDING`: لا يوجد Secret Manager reference صالح.
- `AWAITING_VERIFIED_CONTRACT`: BingX binding موجود، لكن contract/read-only/reconciliation/cancel/recovery غير مكتمل.
- `FORWARD_PAPER_READY`: عقد BingX مراجع مبدئياً ويمكن تقييم read-only/VST/Forward Paper؛ لا يوجد dispatch حي.
- `ADVISORY_READY`: Gemini مرتبط server-side كمستشار شرح فقط.
- `LIVE_BLOCKED`: approval أو drills ناقصة، **أو** لا يوجد live sender مراجع. الإصدار الحالي يبقى هنا حتى لو اكتملت وثائق الموافقة.

`LIVE_GATE_READY` محجوز لإصدار لاحق؛ لا يصدر من codebase الحالي لأن `TradeSignalEngine` و`BingXSwapTestOrderClient` يمنعان live dispatch عمداً.

## ما لا يكفي للتداول الحقيقي

لا يكفي أي مما يلي بمفرده:

- API key / Secret Key.
- نجاة `/order/test` في VST.
- نجاح Gemini commentary.
- ظهور الرسم الخارجي أو وصول OHLCV public.
- `LiveOperationApproval` dataclass غير موثق في persistent/RBAC store.

## بوابة التشغيل المطلوبة قبل Live

1. Secret Manager binding server-side وIP allowlist ومفتاح بلا withdrawals.
2. contract specs وinstrument map وrisk units موثقة، بلا خلط XAU/USD وGC وBTC perpetual.
3. quality/lineage/freshness وclosed-bar validation للبيانات.
4. Forward Paper كامل: idempotency، reject/cancel، disconnect/reconnect، reconciliation للأوامر والمراكز، incident audit، kill switch.
5. RBAC وموافقة بشرية persistent وموقعة زمنياً لكل instrument/limit، مع فصل من يوافق عن من يطور.
6. live adapter مستقل ومراجع، مع pre-trade limits وpost-trade reconciliation. لا يعاد إرسال write بعد timeout؛ يصبح outcome `UNKNOWN` حتى reconcile.
7. change-management منفصل لتفعيل live، ومراقبة alerting وحساب محدود.

## Control plane

`RuntimeControlPlane` يوفر فقط:

```text
GET /healthz
GET /api/v1/control-plane/status
```

ولا يملك route للأوامر أو الحسابات أو secrets. Response لا يكشف API key أو secret reference أو API URLs أو account IDs، و`trade_dispatch=UNAVAILABLE` ثابت.

تشغيل status API محلياً:

```bash
PYTHONPATH=src python -m trading_intelligence.control_plane
# أو بعد التثبيت: trading-intelligence-control-plane
```

الخادم الافتراضي على `0.0.0.0:8080` يحتاج HTTPS reverse proxy عند النشر. Vercel static لا يشغله وحده.
