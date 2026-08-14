#!/usr/bin/env python3
"""
One-off: build a small Search test campaign in test account 6066113101.
- Paused Search campaign, manual CPC, small daily budget
- One ad group with 20 keywords (mixed match types)
- One full RSA (15 headlines, 4 descriptions)
- Geo target: State of New Jersey
- A few campaign-level negative keywords
- Language: English

Safe to run against a TEST account. Creates everything PAUSED.
Run: ~/.openclaw/workspaces/ads-janitor/.venv/bin/python scripts/create_test_campaign.py
"""
import os, sys, uuid
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
import google.auth

# TEST-ONLY SCAFFOLDING. This script seeds fake campaign structure and must
# never run against production. IDs are hardcoded on purpose (it does NOT read
# config.json, so flipping config to production cannot repoint it) and the
# allowlist below is enforced at runtime.
TEST_ACCOUNTS = {"6066113101"}
TEST_MCCS = {"8423405897"}

CUSTOMER_ID = "6066113101"
LOGIN_CID = "8423405897"

if CUSTOMER_ID not in TEST_ACCOUNTS or LOGIN_CID not in TEST_MCCS:
    sys.exit("REFUSING TO RUN: create_test_campaign.py is test-only scaffolding "
             f"and {CUSTOMER_ID} / {LOGIN_CID} is not in the test allowlist.")
NJ_CRITERION_ID = "21164"  # Google geo target constant for New Jersey, USA (verified via API)
EN_LANG_ID = "1000"        # English

def build_client():
    dev_token = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS",
                          "/Users/mitchellclarke/.config/gcloud/application_default_credentials.json")
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/adwords"])
    return GoogleAdsClient(credentials=creds, developer_token=dev_token,
                           login_customer_id=LOGIN_CID, use_proto_plus=True)

# 20 keywords, mixed match types (gives the janitor a realistic ladder to work with)
KEYWORDS = [
    ("running shoes", "BROAD"),
    ("best running shoes", "BROAD"),
    ("trail running shoes", "BROAD"),
    ("marathon shoes", "BROAD"),
    ("mens running shoes", "BROAD"),
    ("womens running shoes", "PHRASE"),
    ("lightweight running shoes", "PHRASE"),
    ("cushioned running shoes", "PHRASE"),
    ("running shoes for flat feet", "PHRASE"),
    ("stability running shoes", "PHRASE"),
    ("neutral running shoes", "PHRASE"),
    ("running shoes sale", "PHRASE"),
    ("cheap running shoes", "EXACT"),
    ("nike running shoes", "EXACT"),
    ("brooks running shoes", "EXACT"),
    ("hoka running shoes", "EXACT"),
    ("asics running shoes", "EXACT"),
    ("running shoes near me", "EXACT"),
    ("road running shoes", "EXACT"),
    ("waterproof running shoes", "EXACT"),
]

HEADLINES = [
    "Premium Running Shoes", "Shop Top Running Shoes", "Free Shipping Over $50",
    "New Arrivals In Stock", "Lightweight & Cushioned", "Trail & Road Running",
    "Shoes For Every Runner", "Best Sellers 2026", "Comfort Meets Performance",
    "Find Your Perfect Fit", "NJ Runners Love Us", "30-Day Easy Returns",
    "Top Brands, Low Prices", "Gear Up & Go Run", "Run Faster Feel Better",
]
DESCRIPTIONS = [
    "Discover premium running shoes built for comfort, speed, and every mile.",
    "Free shipping on orders over $50. Shop top brands with fast delivery.",
    "Lightweight, cushioned, and durable. Find your perfect pair today.",
    "30-day returns and expert fitting help. Gear up for your next run.",
]
CAMPAIGN_NEGATIVES = ["free", "used", "repair", "wholesale"]


def main():
    client = build_client()
    cid = CUSTOMER_ID
    suffix = uuid.uuid4().hex[:6]

    # 1) Budget
    budget_svc = client.get_service("CampaignBudgetService")
    budget_op = client.get_type("CampaignBudgetOperation")
    b = budget_op.create
    b.name = f"Janitor Test Budget {suffix}"
    b.amount_micros = 10_000_000  # $10/day
    b.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    budget_res = budget_svc.mutate_campaign_budgets(customer_id=cid, operations=[budget_op])
    budget_rn = budget_res.results[0].resource_name
    print("✅ Budget:", budget_rn)

    # 2) Campaign (Search, paused, manual CPC)
    camp_svc = client.get_service("CampaignService")
    camp_op = client.get_type("CampaignOperation")
    c = camp_op.create
    c.name = f"Janitor Test Search {suffix}"
    c.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    # Required in newer API versions: EU political advertising declaration
    c.contains_eu_political_advertising = (
        client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    c.status = client.enums.CampaignStatusEnum.PAUSED
    c.manual_cpc = client.get_type("ManualCpc")
    c.campaign_budget = budget_rn
    c.network_settings.target_google_search = True
    c.network_settings.target_search_network = True
    c.network_settings.target_content_network = False
    camp_res = camp_svc.mutate_campaigns(customer_id=cid, operations=[camp_op])
    camp_rn = camp_res.results[0].resource_name
    print("✅ Campaign:", camp_rn)

    # 3) Geo (New Jersey) + Language (English)
    cc_svc = client.get_service("CampaignCriterionService")
    geo_op = client.get_type("CampaignCriterionOperation")
    geo = geo_op.create
    geo.campaign = camp_rn
    geo.location.geo_target_constant = f"geoTargetConstants/{NJ_CRITERION_ID}"
    lang_op = client.get_type("CampaignCriterionOperation")
    lang = lang_op.create
    lang.campaign = camp_rn
    lang.language.language_constant = f"languageConstants/{EN_LANG_ID}"
    # Campaign negative keywords
    neg_ops = []
    for term in CAMPAIGN_NEGATIVES:
        nop = client.get_type("CampaignCriterionOperation")
        n = nop.create
        n.campaign = camp_rn
        n.negative = True
        n.keyword.text = term
        n.keyword.match_type = client.enums.KeywordMatchTypeEnum.BROAD
        neg_ops.append(nop)
    cc_res = cc_svc.mutate_campaign_criteria(customer_id=cid, operations=[geo_op, lang_op] + neg_ops)
    print(f"✅ Campaign criteria: NJ geo + English + {len(CAMPAIGN_NEGATIVES)} negatives ({len(cc_res.results)} total)")

    # 4) Ad group
    ag_svc = client.get_service("AdGroupService")
    ag_op = client.get_type("AdGroupOperation")
    ag = ag_op.create
    ag.name = f"Janitor Test AdGroup {suffix}"
    ag.campaign = camp_rn
    ag.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    ag.cpc_bid_micros = 1_000_000  # $1
    ag.status = client.enums.AdGroupStatusEnum.ENABLED
    ag_res = ag_svc.mutate_ad_groups(customer_id=cid, operations=[ag_op])
    ag_rn = ag_res.results[0].resource_name
    print("✅ Ad group:", ag_rn)

    # 5) 20 keywords
    agc_svc = client.get_service("AdGroupCriterionService")
    kw_ops = []
    mt_enum = client.enums.KeywordMatchTypeEnum
    for text, mt in KEYWORDS:
        op = client.get_type("AdGroupCriterionOperation")
        cr = op.create
        cr.ad_group = ag_rn
        cr.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        cr.keyword.text = text
        cr.keyword.match_type = getattr(mt_enum, mt)
        kw_ops.append(op)
    kw_res = agc_svc.mutate_ad_group_criteria(customer_id=cid, operations=kw_ops)
    print(f"✅ Keywords: {len(kw_res.results)} created")

    # 6) Responsive Search Ad
    aga_svc = client.get_service("AdGroupAdService")
    aga_op = client.get_type("AdGroupAdOperation")
    aga = aga_op.create
    aga.ad_group = ag_rn
    aga.status = client.enums.AdGroupAdStatusEnum.ENABLED
    ad = aga.ad
    ad.final_urls.append("https://www.google.com/")
    for h in HEADLINES:
        asset = client.get_type("AdTextAsset")
        asset.text = h
        ad.responsive_search_ad.headlines.append(asset)
    for d in DESCRIPTIONS:
        asset = client.get_type("AdTextAsset")
        asset.text = d
        ad.responsive_search_ad.descriptions.append(asset)
    ad.responsive_search_ad.path1 = "shoes"
    ad.responsive_search_ad.path2 = "running"
    aga_res = aga_svc.mutate_ad_group_ads(customer_id=cid, operations=[aga_op])
    print("✅ RSA:", aga_res.results[0].resource_name)

    print("\n🎉 Test campaign built (PAUSED). Suffix:", suffix)


if __name__ == "__main__":
    try:
        main()
    except GoogleAdsException as ex:
        print("❌ GoogleAdsException:", ex.error.code().name if hasattr(ex,'error') else ex)
        for e in ex.failure.errors:
            print("   -", e.message)
            if e.location:
                for fpe in e.location.field_path_elements:
                    print("      @", fpe.field_name)
        sys.exit(1)
