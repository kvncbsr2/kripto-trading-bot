# KRIPTO AGENT — Walk-Forward Validation Framework

## 1. The Overfitting Problem in Algorithmic Trading
Strategies that perform exceptionally well on backtests frequently fail in live markets due to:
* Data leakage between train and test sets.
* Parameter over-optimization (curve fitting).
* Failure to adapt across changing market regimes (e.g. Bull Run vs. Sideways Consolidation).

---

## 2. Walk-Forward Architecture
To guarantee genuine predictive edge, `WalkForwardValidator` divides data into sliding chronological folds:

```text
Fold 1: [--- Train (60%) ---] [Val (20%)] [Test (20%)]
Fold 2:       [--- Train (60%) ---] [Val (20%)] [Test (20%)]
Fold 3:             [--- Train (60%) ---] [Val (20%)] [Test (20%)]
```

### Protocol:
1. **Train (In-Sample)**: Strategy parameters are evaluated.
2. **Validation**: Best candidate configurations are selected.
3. **Test (Out-of-Sample)**: Strategy is evaluated strictly on unseen future data with zero parameter adjustment.
4. **Window Step**: The window shifts forward in time, repeating the cycle.

---

## 3. Walk-Forward Efficiency (WFE)
The platform measures the Walk-Forward Efficiency Ratio:
$$WFE = \frac{\text{Annualized Return}_{\text{Out-of-Sample}}}{\text{Annualized Return}_{\text{In-Sample}}}$$
* $WFE \ge 60\%$: Strategy demonstrates robust, non-overfitted predictive edge.
* $WFE < 50\%$: Strategy is curve-fitted and must be discarded.
