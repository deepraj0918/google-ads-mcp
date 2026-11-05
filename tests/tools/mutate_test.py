# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test cases for the mutate (write) tools."""

import unittest
from unittest.mock import MagicMock, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp.tools import mutate


class _Response:
    def __init__(self, names):
        self.results = [MagicMock(resource_name=n) for n in names]


class MutateTestBase(unittest.TestCase):
    """Uses a real client to build requests; only RPCs are mocked."""

    def setUp(self):
        self.client = GoogleAdsClient(
            credentials=AnonymousCredentials(),
            developer_token="test",
            use_proto_plus=True,
        )
        self.calls = {}
        real_get_service = self.client.get_service

        def fake_service(client, name):
            service = real_get_service(name)
            for attr in dir(type(service)):
                if attr.startswith("mutate"):
                    mock = MagicMock(return_value=_Response(["customers/1/x"]))
                    setattr(service, attr, mock)
                    self.calls[attr] = mock
            return service

        patches = [
            patch(
                "ads_mcp.utils.get_googleads_client", return_value=self.client
            ),
            patch("ads_mcp.tools.mutate._service", side_effect=fake_service),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def kwargs(self, method):
        return self.calls[method].call_args.kwargs


class TestUpdates(MutateTestBase):
    def test_campaign_status_defaults_to_validate_only(self):
        out = mutate.update_campaign_status("123-456-7890", "55", "PAUSED")
        kw = self.kwargs("mutate_campaigns")
        self.assertTrue(kw["validate_only"])
        self.assertEqual(kw["customer_id"], "1234567890")
        op = kw["operations"][0]
        self.assertEqual(
            op.update.resource_name, "customers/1234567890/campaigns/55"
        )
        self.assertEqual(op.update.status.name, "PAUSED")
        self.assertEqual(
            list(op.update_mask.paths), ["resource_name", "status"]
        )
        self.assertFalse(out["applied"])
        self.assertEqual(out["resource_names"], ["customers/1/x"])

    def test_campaign_status_apply(self):
        out = mutate.update_campaign_status(
            "1", "2", "ENABLED", validate_only=False
        )
        self.assertFalse(self.kwargs("mutate_campaigns")["validate_only"])
        self.assertTrue(out["applied"])

    def test_budget(self):
        mutate.update_campaign_budget("1", "9", 25_000_000)
        op = self.kwargs("mutate_campaign_budgets")["operations"][0]
        self.assertEqual(op.update.amount_micros, 25_000_000)
        self.assertIn("amount_micros", op.update_mask.paths)

    def test_budget_rejects_non_positive(self):
        with self.assertRaises(ToolError):
            mutate.update_campaign_budget("1", "9", 0)

    def test_keyword_bid_and_status(self):
        mutate.update_keyword(
            "1", "2", "3", status="PAUSED", cpc_bid_micros=1_500_000
        )
        op = self.kwargs("mutate_ad_group_criteria")["operations"][0]
        self.assertEqual(
            op.update.resource_name, "customers/1/adGroupCriteria/2~3"
        )
        self.assertEqual(op.update.cpc_bid_micros, 1_500_000)
        self.assertEqual(op.update.status.name, "PAUSED")

    def test_keyword_requires_change(self):
        with self.assertRaises(ToolError):
            mutate.update_keyword("1", "2", "3")

    def test_ad_status(self):
        mutate.update_ad_status("1", "2", "3", "ENABLED")
        op = self.kwargs("mutate_ad_group_ads")["operations"][0]
        self.assertEqual(op.update.resource_name, "customers/1/adGroupAds/2~3")

    def test_invalid_id(self):
        with self.assertRaises(ToolError):
            mutate.update_ad_group_status("abc", "2", "PAUSED")


class TestCreates(MutateTestBase):
    def test_add_keywords(self):
        mutate.add_keywords(
            "1",
            "2",
            [
                mutate.KeywordInput(text="red shoes", match_type="EXACT"),
                mutate.KeywordInput(text="blue shoes", cpc_bid_micros=900000),
            ],
        )
        ops = self.kwargs("mutate_ad_group_criteria")["operations"]
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0].create.keyword.match_type.name, "EXACT")
        self.assertEqual(ops[0].create.status.name, "PAUSED")
        self.assertEqual(ops[1].create.keyword.match_type.name, "PHRASE")
        self.assertEqual(ops[1].create.cpc_bid_micros, 900000)

    def test_add_negative_keywords(self):
        mutate.add_negative_keywords("1", "5", ["free", "cheap"])
        ops = self.kwargs("mutate_campaign_criteria")["operations"]
        self.assertTrue(all(o.create.negative for o in ops))
        self.assertEqual(ops[0].create.campaign, "customers/1/campaigns/5")

    def test_create_search_campaign(self):
        mutate.create_search_campaign("1", "Test", 10_000_000)
        ops = self.kwargs("mutate")["mutate_operations"]
        budget = ops[0].campaign_budget_operation.create
        campaign = ops[1].campaign_operation.create
        self.assertEqual(budget.resource_name, "customers/1/campaignBudgets/-1")
        self.assertEqual(campaign.campaign_budget, budget.resource_name)
        self.assertEqual(campaign.status.name, "PAUSED")
        self.assertEqual(campaign.advertising_channel_type.name, "SEARCH")
        self.assertIn("manual_cpc", campaign)
        self.assertEqual(
            campaign.contains_eu_political_advertising.name,
            "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        )

    def test_create_ad_group(self):
        mutate.create_ad_group("1", "5", "AG", cpc_bid_micros=1_000_000)
        op = self.kwargs("mutate_ad_groups")["operations"][0]
        self.assertEqual(op.create.campaign, "customers/1/campaigns/5")
        self.assertEqual(op.create.type_.name, "SEARCH_STANDARD")

    def test_create_rsa(self):
        mutate.create_responsive_search_ad(
            "1",
            "2",
            ["H1", "H2", "H3"],
            ["D1", "D2"],
            ["https://example.com"],
            path1="shoes",
        )
        op = self.kwargs("mutate_ad_group_ads")["operations"][0]
        rsa = op.create.ad.responsive_search_ad
        self.assertEqual([h.text for h in rsa.headlines], ["H1", "H2", "H3"])
        self.assertEqual(rsa.path1, "shoes")
        self.assertEqual(op.create.status.name, "PAUSED")

    def test_create_rsa_validates_counts(self):
        with self.assertRaises(ToolError):
            mutate.create_responsive_search_ad(
                "1", "2", ["H1"], ["D1", "D2"], ["https://example.com"]
            )


class TestResult(unittest.TestCase):
    def test_google_ads_service_response(self):
        from google.ads.googleads.v24.services.types import google_ads_service

        resp = google_ads_service.MutateGoogleAdsResponse()
        r = google_ads_service.MutateOperationResponse()
        r.campaign_result.resource_name = "customers/1/campaigns/7"
        resp.mutate_operation_responses.append(r)
        out = mutate._result(resp, False)
        self.assertEqual(out["resource_names"], ["customers/1/campaigns/7"])


if __name__ == "__main__":
    unittest.main()
