# معايير الاختبار والصدق العلمي

## ما اختُبر فعلاً في هذا الفرع

يشغّل الأمر التالي اختبارات وحدات محلية لا تحتاج إنترنت أو بيانات سوق:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

الحالات الحالية (73 اختباراً محلياً):

- رفض `future/unclosed` bar لمنع look-ahead.
- رفض ICT evidence على بيانات غير صالحة.
- baseline كمي على trend اصطناعي مغلق فقط.
- رفض volume signal إذا كان الحجم غائباً/صفرياً.
- عدم تضخيم ثلاث إشارات ICT/SMC/Price Action المترابطة إلى ثلاث أدلة مستقلة.
- اشتراط مجموعتين مستقلتين على الأقل قبل قرار اتجاهي بحثي.
- إيقاف المخاطر عند تجاوز daily loss.
- منع `TradeSignal` في `ANALYSIS_ONLY` حتى مع decision/risk صالحين.
- metrics بلا profit factor مصطنع، temporal walk-forward، وMonte Carlo قابل للإعادة بالبذرة.
- صلاحية source/relations وحقول concept/strategy المنظمة في قاعدة المعرفة، واسترجاع deterministic يبين حقول التطابق، وتماثل النسخ المرفقة بالحزمة مع السجل canonical.
- استيراد CSV مع SHA-256 وتحويل timezone إلى UTC، وتجميع intraday لا يملأ bucket غير مكتمل.
- replay history يثبت أن الإشارة لا ترى المستقبل وتدخل عند next-open فقط.
- بار يلمس stop وtarget يعامل `STOP_FIRST_AMBIGUOUS`، وليس المسار الرابح المفترض.
- محرك backtest يرفض metadata لوصف live feed؛ replay مقصور على historical/synthetic.
- سلسلة Ledger hash-chain سليمة وتكتشف تعديل payload.
- Paper simulator يثبت idempotency وkill switch وexit الورقي المحافظ.
- circuit breakers لمتتاليات الخسارة وevent-risk embargo.
- `PortfolioRiskBook`: خسائر محققة UTC يومياً/أسبوعياً، drawdown، exposure، وتركيز correlation group.
- gap-buffered sizing وgap-risk embargo لا يتجاوزان Risk Management Engine.
- attribution للأداء وdrawdown recovery حسب strategy/timeframe/session على fixtures زمنية.
- عقد `BarRequest` يرفض نطاقاً زمنياً غير متزايد قبل أي provider implementation.
- Operational monitor يميز feed stale وdata-quality incident ويعيد إلى healthy بعد heartbeat/resolve.
- execution lifecycle يحجب `ANALYSIS_ONLY`، ويطلب `ExecutionPolicy` صريحة للـPaper (allowlist/product spec/notional/worst-case risk/active-order/gross-notional limits)، ويمنع تكرار `client_order_id` أو إعادة استخدامه لـimmutable intent مختلف.
- lifecycle يسجل hash-chained audit عند ربطه بـ`ResearchLedger`، أو يسجل transition ذرياً/idempotent في `SupabaseLifecycleStore` عند ربطه صراحةً؛ يرفض الجمع بين sinkين حتى لا يخفي failure موزعاً. fixture Supabase يثبت URL/secret-reference boundary وcontiguous RPC payload وduplicate retry ورفض local advance عند HTTP failure.
- lifecycle يحوّل in-flight إلى `UNKNOWN` عند الانقطاع، ويرفض fill reconciliation بلا cumulative quantity موثق، ويحافظ على UNKNOWN أثناء emergency stop حتى المصالحة.
- dashboard contract يثبت أن وضع `ANALYSIS_ONLY` و`NO TRADE` ظاهر، وأن الرسم الخارجي opt-in داخل iframe sandbox ولا يكتب مدخلاً للمحركات، وأن security headers صالحة.
- OANDA v20 adapter يُختبر بـtransport fixture: يطلب HTTPS/read-only path، يحفظ hash/lineage، ولا يمرر token إلى source URL، ويرفض bar غير المكتمل من output.
- `MarketDataPipeline` يحجر gaps وفق policy ولا يملأها، ويسجل receipt وفشل/quality؛ ويصنف exact replay ويحجر revision متضارب وfeed حي stale، ولا يبني snapshot إلا من bars مقبولة exact-deduplicated؛ و`PeriodicPollingCoordinator` لا يبدأ daemon ويعمل فقط عند `poll_due()` صريحة.
- range-outlier classifier يضع علامة anomaly ولا يصحح السعر، وVolume Profile engine يرفض اختلاق VAP/TPO/POC من OHLCV.
- Structure engine يضع BOS/CHoCH/MSS candidate على bars مغلقة فقط؛ وcalendar event gate يختبر embargo زمنيًا على event موثق.
- paper signal المنظم يحمل conditions ذات حالات `MET/BLOCKED/UNKNOWN` وشرحًا صريحًا أن confidence ليس احتمال ربح.
- Wyckoff spring candidate وFibonacci confirmed-swing retracement يعملان على fixtures مغلقة فقط؛ ولا يعلنان phase أو سببية ratio.
- Fundamental event engine يعلن calendar-risk context فقط، وMarket/Volume Profile وClassical Patterns يخرجان missing-input report بدلاً من اختلاق تحليل.
- Engine catalog موصول بـKnowledgeBase: كل concept ID معلن للمحرك يُتحقق من وجوده ومصدره وعلاقاته عند تحميل السجل؛ Liquidity proxy وSession context وML backlog تصدر reports مستقلة، وتسجيل orchestrator hash-chained يحفظ حتى results ذات missing input.
- `InstrumentRegistry` يثبت فصل XAU/USD عن GC ويدرج FX والمؤشرات والأسهم والعملات الرقمية من دون اختراع vendor mapping أو مواصفات تنفيذ.
- `MarketResearchAgent` ينسق مجموعتي evidence مستقلتين ومرجع ATR مغلقاً ثم يمر عبر risk gate إلى `PAPER_CANDIDATE` فقط؛ لا ينتج live order.
- عقود BingX/Gemini تختبر أن السر مرجع secret-manager لا قيمة، وأن BingX يحتاج عقد API/Forward-Paper/reconciliation/emergency drills وموافقة زمنية؛ ومع ذلك يبقى `LIVE_BLOCKED` لأن لا sender حي في المستودع.
- BingX USDT-M provider يختبر endpoint public/fallback GET وheader الرسمي وOHLCV/lineage، بينما VST `/order/test` يختبر canonical ASCII/HMAC وتبقى محاولة live dispatch مرفوضة صراحة.
- `EnvironmentSecretResolver` لا يقبل إلا `sm://env/UPPERCASE_NAME` server-side، وGemini chart advisory يختبر completed-candle brief وprompt lineage من دون تسريب المفتاح ومن دون سلطة على decision/risk/execution.
- control plane WSGI يختبر status أحادي origin ومخفي المراجع، مع `ANALYSIS_ONLY`/`UNAVAILABLE` و`no-store` وGET-only؛ ويعيد أي route للأوامر 404.

هذه اختبارات سلوك برنامج وfixture اصطناعية، **وليست Backtest على السوق ولا إثبات ربحية**.

## مصفوفة قبول لكل مكوّن

| المكوّن | Unit/contract | Integration/replay | Research validation | Production monitoring |
|---|---|---|---|---|
| Adapter | parser/schema/auth mock | raw-to-normalized fixture مع checksum | مقارنة bars مع vendor | latency, stale rate, gaps |
| Data QA | duplicates/OHLC/UTC | sessions, DST, roll, corrections | أثر data-quality على النتيجة | quality score + quarantine |
| School engine | known event fixture وinvalidation | event timestamps/replay | event study + OOS + ablation | firing rate/drift |
| Central | correlation cap/conflict/NO_TRADE | multi-TF snapshot | incremental value/ calibration | disagreement/decision distribution |
| Risk | boundary/property tests | simulated portfolio | stress/Monte Carlo/DD | hard-limit alerts/kill switch |
| Backtest | cost/fill/next-bar semantics | golden fixtures | walk-forward/final holdout/PBO | paper-vs-model slippage |
| Paper | idempotency/recovery | provider/broker reconciliation | prospective hit/expectancy | mismatch/late events |

## إلزاميات Backtest قبل أي ادعاء أداء

1. Raw data source ونسخته/سياسته ومجاله الزمني موثقة.
2. لا bar أو event أو revised value من المستقبل؛ test يعيد snapshot عند كل زمن.
3. bid/ask/spread/commission/slippage/financing/roll/corporate actions بحسب الأداة.
4. سياسة fill مذكورة (next open/limit/stop/tick) ولا تدخل على سعر غير قابل للتنفيذ.
5. كل تجربة/parameter محفوظة؛ ليس الفائز فقط.
6. train/validation/test زمني؛ final holdout لا يستخدم للتصميم.
7. تقارير: trades, exposure, PF, expectancy, DD, Sharpe/Sortino, turnover, regime/session/timeframe/instrument slices.
8. sensitivity، Monte Carlo، baseline comparison، PBO/DSR إن كانا مناسبين.
9. محاكاة انقطاعات/فجوات/إشارات متأخرة وأخبار/فجوات نهاية الأسبوع.

أي شرط غائب يجعل النتيجة `RESEARCH_INCOMPLETE` لا `Candidate`.
