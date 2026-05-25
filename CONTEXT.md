# Bookkeeping Agent

Bookkeeping Agent receives payment evidence, turns likely transactions into bookkeeping candidates, and lets the user decide whether each candidate should become a recorded transaction.

## Language

**Bookkeeping Candidate**:
A transaction-like item recognized from payment evidence that still needs the user's decision before it becomes a recorded transaction.
_Avoid_: Draft row, unconfirmed table data

**Confirmed Transaction**:
A bookkeeping candidate that the user has accepted as a real recorded transaction.
_Avoid_: Approved candidate, final card

**Cancellation**:
The user's decision to discard a bookkeeping candidate before it becomes a confirmed transaction. A cancellation means the candidate should no longer exist as a bookkeeping item, and it does not apply to confirmed transactions; message history may still show what was discarded.
_Avoid_: Revocation, deleted transaction, rejected transaction

## Example Dialogue

Developer: "This screenshot produced a bookkeeping candidate. Should the card show confirmation controls?"

Domain Expert: "Yes, because it is still waiting for the user's decision."

Developer: "The user clicked cancel before confirming it. Is that now a cancelled transaction?"

Domain Expert: "No. It never became a confirmed transaction; cancellation discards the candidate."
