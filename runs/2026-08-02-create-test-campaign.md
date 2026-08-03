# Run: Create test campaign — 2026-08-02

Account: 6066113101 (test) under MCC 8423405897. USD, America/New_York, test=True.
Script: scripts/create_test_campaign.py (RSA re-added separately after policy fix).

## Built (all PAUSED at campaign level)
- Campaign: "Janitor Test Search bba54b" id 24090872616 | SEARCH | $10/day | Manual CPC | Search+partners, no display
- Budget: $10/day standard
- Geo: New Jersey (geoTargetConstants/21164)
- Language: English (languageConstants/1000)
- Campaign negatives (broad): free, used, repair, wholesale
- Ad group: "Janitor Test AdGroup bba54b" id 201976541274 | $1 CPC
- Keywords: 20 total — BROAD:5, PHRASE:7, EXACT:8 (running-shoes theme)
- RSA: 15 headlines, 4 descriptions, paths shoes/running, final_url https://www.google.com/

## Gotchas learned (for future scripts)
- API v25 requires campaign.contains_eu_political_advertising = EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
- NJ geo target constant = 21164 (verify geo IDs via geo_target_constant query, don't guess)
- example.com final URLs get PROHIBITED policy disapproval — use a real reachable URL even in test
- Cleaned 2 orphan budgets from failed partial runs (reference_count=0)
