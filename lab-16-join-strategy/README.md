# Lab 16: Spark join strategy and skew

## Scenario
A card-transactions fact table joins a merchant dimension. One merchant holds most rows, so a plain shuffle join leaves one task running far longer than the rest.

| Situation | Strategy |
|---|---|
| One side under the broadcast threshold | broadcast |
| Large both sides, one hot key | salted shuffle (AQE skew join also helps) |
| Large both sides, even keys | sort-merge |

## How to run (local, no AWS)
```
pip install pytest
python -m pytest -q lab-16-join-strategy
```
