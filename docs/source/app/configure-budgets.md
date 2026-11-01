# Configure GPU prices and budgets

Set hourly GPU prices and a monthly budget so your organization can see its experiment spend.

## What the budget measures

Flock estimates experiment spend from recorded GPU hours and the hourly prices an admin assigns. GPU hours are the run time multiplied by its GPU count: two GPUs active for three hours contribute six GPU hours. This is the accounting view of your experiments, not a scheduler that allocates machines.

A monthly budget gives that spend a comparison point. Prices determine estimated cost, and the currency is a label rather than an exchange-rate conversion. Check the hardware count before interpreting totals, particularly for multi-node jobs. The [accounting reference](budget-behavior) defines heartbeat gaps and UTC month boundaries.

## Before you start

You need organization admin access. Check the GPU model and count on representative runs first; multi-node jobs or restricted visible devices may need a correction.

## Configure and check spend

1. Open the organization's settings and **Budget**.
2. Set the currency label, monthly budget and hourly price per GPU type. Save the changes.
3. Open the budget view and check the project's spend and remaining budget.
4. Review **Budget warnings** in settings. Set prices for unpriced types and correct GPU information on runs with unknown hardware.
5. Recheck the budget after corrections. Dismiss a warning only after reviewing the underlying runs or GPU types.

Use **Spend of deleted projects** if an admin needs to exclude a deleted project's spend; deleting a run by itself does not refund its cost.

The [accounting reference](budget-behavior) explains UTC month boundaries, heartbeat gaps, historical recalculation, warning fingerprints and exclusions. The [Home and budget API](api/home) supports scripted reads and updates.
