# معايير الاختبار والصدق العلمي

## ما اختُبر فعلاً في هذا الفرع

يشغّل الأمر التالي اختبارات وحدات محلية لا تحتاج إنترنت أو بيانات سوق:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

الحالات الحالية (14 اختباراً):

- رفض `future/unclosed` bar لمنع look-ahead.
- رفض ICT evidence على بيانات غير صالحة.
- baseline كمي على trend اصطناعي مغلق فقط.
- رفض volume signal إذا كان الحجم غائباً/صفرياً.
- عدم تضخيم ثلاث إشارات ICT/SMC/Price Action المترابطة إلى ثلاث أدلة مستقلة.
- اشتراط مجموعتين مستقلتين على الأقل قبل قرار اتجاهي بحثي.
- إيقاف المخاطر عند تجاوز daily loss.
- منع `TradeSignal` في `ANALYSIS_ONLY` حتى مع decision/risk صالحين.
- metrics بلا profit factor مصطنع، temporal walk-forward، وMonte Carlo قابل للإعادة بالبذرة.
- صلاحية مراجع source/relations في قاعدة المعرفة، وتماثل نسختها المرفقة بالحزمة مع السجل canonical.

هذه اختبارات سلوك برنامج، **وليست Backtest ولا إثبات ربحية**.

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
