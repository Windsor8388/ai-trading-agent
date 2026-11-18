# عقد Trade Signal المنظم

## حد الصلاحية

`TradeSignalEngine` يحوّل `Decision` + `MarketSnapshot` + levels + risk إلى **مرشح Paper فقط**. وضع `LIVE` يرفض دائماً في هذا المستودع. لا ينشئ الأمر ولا يستدعي provider أو broker.

## الحقول الفعلية

| الحقل | المصدر/المعنى |
|---|---|
| `instrument`, `timeframe`, `data_source`, `created_at` | هوية لقطة المصدر، UTC، ولا تقبل كـcurrent إلا مع feed متحقق |
| `action` | `BUY` / `SELL` فقط عند اكتمال الشروط؛ وإلا `NO_TRADE` |
| `strategy_ids` | إصدارات قواعد/محركات ساهمت في المرشح |
| `levels` | `entry`, `stop_loss`, `targets`, `entry_condition`؛ reference levels وليست fill أو توصية تنفيذ |
| `risk` | approval، quantity، cash risk، worst-case risk، reward/risk؛ من Risk Engine مستقل |
| `confidence` | اتفاق أدلة de-correlated تحت policy، **ليس احتمال ربح أو توقع عائد** |
| `supporting_evidence` / `opposing_reasons` | أدلة قابلة للمراجعة وأسباب الرفض/المعارضة |
| `invalidation_conditions` | ما يلغي الفرضية، وليس stop تنفيذ فعلياً بحد ذاته |
| `conditions` | قائمة منظمة `condition_id/category/state/detail`، وحالاتها `MET` أو `BLOCKED` أو `UNKNOWN` |
| `status` | `PAPER_CANDIDATE` أو `BLOCKED`؛ لا توجد قيمة live-ready |

## شروط الإصدار

يتطلب `PAPER_CANDIDATE` حالياً:

1. قرار مركزي اتجاهي بأدلة مستقلة وفق `CentralPolicy`.
2. candles مغلقة وسليمة عند `as_of`.
3. source في `PAPER` أو `LIVE` **وموسوم `live_feed_verified=true`**؛ لا يكفي رسم خارجي أو CSV تاريخي.
4. وضع `OperatingMode.PAPER`، وليس `ANALYSIS_ONLY` أو `LIVE`.
5. levels صريحة وRisk Management Engine موافق على الاتجاه والوقف والهدف/spec/portfolio limits.

حتى عند تحقق ذلك، لا يكفي Trade Signal للإرسال: `ExecutionPolicy` و`OrderLifecycleManager` يضيفان allowlist ومواصفات المنتج والحدود وidempotency وaudit/reconciliation قبل دورة Paper. لا توجد bridge من signal إلى broker في هذا المشروع.

## الأخبار والأحداث

`EconomicEventRiskGate` يحوّل `EconomicEvent` supplied إلى `EventRiskAssessment`. عند `active=true` يجب أن ينعكس ذلك في `PortfolioState(event_risk_active=True)` قبل استدعاء Risk Engine؛ هذا يحجب entry جديد. gate لا يجلب تقويماً ولا يدّعي أن عدم وجود events يعني عدم وجود أخبار.
