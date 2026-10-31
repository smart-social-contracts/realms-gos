# Examples



1. Retirement Pensions
Citizens receive periodic payments upon reaching a certain age.
Eligibility might be based on age, past contributions, or roles.
Modeled using: Mandate, Trade, Instrument, Certificate.


Mandate => Task + TaskSchedule => Instrument[ckBTC] => Trade => Certificate(Trade)
(prompt)   Codex


2. Realm rents out a land plot to organization for a period of time

Mandate => Task + TaskSchedule => check if payment (Trade) received => if not => Dispute => Organization
            Codex


3. Driving license issuance, renovation and revocation
Payment => Task => License 

4. Tax collection and payment of departments and services
Task + TaskSchedule => Trades



Organization(
    justice administrator
)

Organization(
    justice executor
)




Mandate(
    name="land_lease_policy",
    description="Allows organization ABC to rent the land plot in (X,Y) for 1 year.",
    category="land",
    governed_by="land_law_2024"
)

Proposal(
    name="land_lease_policy",
    description="Allows organization ABC to rent the land plot in (X,Y) for 1 year.",
    category="land",
    governed_by="land_law_2024"
)

Contract(
    mandate="land_lease_policy",
    status="active",
    metadata="{}"
)

12 x
Trade(
    user_a="user_a",
    user_b="user_b",
    instruments_a=["instrument_a"],
    instruments_b=["instrument_b"],
    mandate="land_lease_policy",
    metadata="{}"
)

Dispute(
    trade="trade_1",
    status="pending",
    metadata="{}"
)

Contract(
    # between the claimant and the justice administrator
) 