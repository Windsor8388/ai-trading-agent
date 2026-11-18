# إعادة التشغيل التاريخية وPaper Simulation

**الإصدار:** 0.2.0 · **الحالة:** محرك replay محلي وCSV importer وPaper simulator داخل الذاكرة.
**ليس هناك اتصال بمزود سوق أو وسيط، ولم تُنفذ أي نتيجة Backtest على بيانات سوق حقيقية في هذا المشروع.**

## ما أصبح منفذاً

| المكوّن | الملف | السلوك الفعلي |
|---|---|---|
| CSV OHLCV importer | `providers/csv_ohlcv.py` | يقرأ ملف مستخدم ISO-8601، يحوله إلى UTC، يرفض الصفوف السيئة، ويصدر SHA-256 manifest |
| Resampling محافظ | `data.py` | يجمع intraday فقط، ولا يملأ gaps أو يبني D1/W1/MN بلا session policy |
| Event-driven replay | `backtest/engine.py` | callback يرى bars المنتهية فقط؛ الإشارة في `t` تدخل عند `t+1 open` فقط |
| Conservative fills | `backtest/engine.py` | slippage/commission، gap-at-stop، وstop-first عندما يلمس bar واحد الوقف والهدف |
| Audit ledger | `research/ledger.py` | JSONL append-only مع سلسلة SHA-256 تكشف العبث |
| Paper execution simulator | `paper.py` | state machine محلي، idempotency، kill switch، وOHLC exits محافظة؛ ليس broker |

## ضمانات منع الانحياز المطبقّة

1. `signal_callback` يستقبل `MarketSnapshot` فيه `candles[:i+1]` فقط و`as_of=close_time[i]`.
2. لا يصبح instruction صفقة داخل البار الذي أنشأه؛ ينتظر افتتاح البار التالي.
3. إذا لمس `high/low` الهدف والوقف داخل البار نفسه، فالنتيجة `STOP_FIRST_AMBIGUOUS`، لا يأخذ المحرك المسار الأكثر ربحاً.
4. gap خلال الوقف يستخدم سعراً ليس أفضل من افتتاح البار عند الحاجة.
5. إذا جعل gap سعر الدخول الوقف أو الهدف غير صالحين، تسجل instruction كمرفوضة بدلاً من تعديل مستوياتها تاريخياً.
6. input غير مرتب/مكرر/غير مكتمل يرفض، وdata gaps توقف التشغيل افتراضياً (`allow_data_warnings=False`).

هذه ضمانات محاكاة bar-level فقط. لا تحل محل tick data، queue position، partial fills، market impact، spread متغير، borrow/funding أو roll. يجب إعلان تلك القيود في كل experiment.

## شكل استراتيجية الـreplay

```python
from trading_intelligence.backtest import BacktestInstruction
from trading_intelligence.models import DecisionAction, ProposedLevels

# يجب أن تعتمد فقط على snapshot.candles(timeframe) الظاهرة هنا.
def strategy(snapshot):
    bars = snapshot.candles("H1")
    if len(bars) < 60:
        return None
    # مثال بنيوي فقط، وليس استراتيجية موصى بها:
    if bars[-1].close > max(bar.high for bar in bars[-21:-1]):
        return BacktestInstruction(
            action=DecisionAction.BUY,
            levels=ProposedLevels(
                entry=bars[-1].close,       # يسجل كمرجع؛ fill الفعلي هو next open
                stop_loss=bars[-1].close - 2.0,
                targets=(bars[-1].close + 4.0,),
                entry_condition="closed-bar breakout",
            ),
            strategy_id="research.breakout.v1",
            confidence=0.0,
        )
    return None
```

لا يستخدم المحرك `levels.entry` كـfill مزعوم؛ يسجل قرار الاستراتيجية ثم يملأ عند next open بسعر مضرّب بالـslippage. ويجب أن تكون قيمة `contract_multiplier` و`quantity` صحيحة للمنتج/الوسيط الفعليين، خصوصاً XAU/USD؛ لا تنقل مواصفات GC إلى spot/CFD.

## CSV ingestion آمن وقابل للإعادة

```python
from datetime import datetime, timezone
from pathlib import Path
from trading_intelligence.providers import CSVBarSpec, import_ohlcv_csv

result = import_ohlcv_csv(CSVBarSpec(
    path=Path("data/xauusd_h1.csv"),
    timeframe="H1",
    provider="user-licensed-provider",
    instrument="XAUUSD",
    instrument_kind="XAUUSD_SPOT",
    price_component="MID",
), retrieved_at=datetime.now(timezone.utc))

print(result.manifest.dataset_sha256)
print(result.manifest.quality)
```

الملف يجب أن يحتوي header افتراضياً: `timestamp,open,high,low,close,volume`. يسمح بتعديل أسماء الأعمدة وtimezone في `CSVBarSpec`. import لا يعني أن المصدر مرخص أو أن الرموز/الحجم قابلة للمقارنة؛ تلك مسؤولية `FeedMetadata` وmanifest والمراجعة البشرية.

## تشغيل Backtest بعد تجميد الفرضية

```python
from trading_intelligence.backtest import BacktestConfig, BacktestEngine

engine = BacktestEngine(BacktestConfig(
    initial_cash=10_000,
    quantity=1,                   # لا تستخدم قيمة حقيقية بلا product spec
    contract_multiplier=1,        # مثال بحثي فقط
    periods_per_year=24 * 252,    # H1 للأسواق التي تتبع هذا الافتراض؛ وثقه
    commission_bps=1.0,
    slippage_bps=2.0,
))
run = engine.run(result.candles, result.manifest.feed, "H1", strategy)
print(run.summary)
```

قبل حفظ نتيجة، أضف إلى `ResearchLedger` على الأقل: data hash، الفترة، strategy id/version، parameters، costs، universe، code commit، وسبب اختيار النموذج. `ResearchLedger` يكشف تعديل ملفه محلياً، لكنه ليس بديلاً عن object storage غير قابل للتعديل أو فصل الصلاحيات في الإنتاج.

## Paper simulation، لا Paper broker

`PaperExecutionSimulator` يقبل فقط `TradeSignal` بحالة `PAPER_CANDIDATE` مع risk assessment صحيح و`InstrumentRiskSpec` مطابق. يضمن:

- `client_order_id` idempotent: إعادة الإرسال تعيد نفس الكائن ولا تنشئ أمراً ثانياً.
- حالات واضحة: `ACCEPTED`, `OPEN`, `CLOSED`, `REJECTED`, `CANCELLED`.
- kill switch يلغي الأوامر الورقية المعلقة ويمنع قبول طلبات جديدة.
- لا يفتح شبكة ولا يعرف credentials ولا يدعي معالجة أوامر حقيقية.

لا تستخدمه على حساب أو بيانات حية قبل ربط provider read-only، حفظ prediction قبل outcome، reconciliation، مراجعة مخاطر، وموافقة صريحة لاحقة.

## تفسير النتائج

`BacktestSummary` يفرق بين:

- `trade_*`: عدد الصفقات، صافي الربح، expectancy، profit factor وwin rate من الصفقات المغلقة.
- `equity_bar_metrics`: total return / max drawdown / Sharpe / Sortino محسوبة من تغير equity لكل bar وفق `periods_per_year` الذي حدده experiment.

لا تقارن Sharpe بين أطر/أسواق إذا كان annualization/calendar مختلفاً، ولا تعرض `None` كأنه صفر أو قيمة إيجابية. النتيجة الواحدة ليست قبولاً: يلزم walk-forward وfinal OOS وstress costs وMonte Carlo وforward paper حسب [`VALIDATION_AR.md`](VALIDATION_AR.md).
