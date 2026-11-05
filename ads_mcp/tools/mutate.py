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

"""Tools that write (mutate) Google Ads account data.

Every tool defaults to `validate_only=True`: the request is checked by the
Google Ads API but nothing is changed. Callers must pass `validate_only=False`
to apply the change. Statuses are limited to ENABLED and PAUSED; nothing here
removes entities, and every new entity is created PAUSED unless asked
otherwise.
"""

from typing import Any, Dict, List, Literal, Optional

from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import ads_mcp.utils as utils
from ads_mcp.coordinator import mcp
from ads_mcp.mcp_header_interceptor import MCPHeaderInterceptor

Status = Literal["ENABLED", "PAUSED"]
MatchType = Literal["EXACT", "PHRASE", "BROAD"]

_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True
)
_CREATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False
)


class KeywordInput(BaseModel):
    """A keyword to add."""

    text: str = Field(description="Keyword text, e.g. 'running shoes'.")
    match_type: MatchType = Field(default="PHRASE")
    cpc_bid_micros: Optional[int] = Field(
        default=None,
        description="Optional keyword-level max CPC in micros (1 unit = 1,000,000).",
    )


def _id(value: str) -> str:
    """Strips hyphens/spaces so '123-456-7890' becomes '1234567890'."""
    cleaned = str(value).replace("-", "").replace(" ", "")
    if not cleaned.isdigit():
        raise ToolError(f"Invalid id '{value}': expected digits only.")
    return cleaned


def _service(client, name: str):
    return client.get_service(name, interceptors=[MCPHeaderInterceptor()])


def _result(response, validate_only: bool) -> Dict[str, Any]:
    """Collects resource names from a service or GoogleAdsService response."""
    names = [r.resource_name for r in getattr(response, "results", [])]
    for op in getattr(response, "mutate_operation_responses", []):
        result_field = type(op).pb(op).WhichOneof("response")
        if result_field:
            names.append(getattr(op, result_field).resource_name)
    return {
        "validate_only": validate_only,
        "applied": not validate_only,
        "resource_names": [n for n in names if n],
    }


def _run(fn, validate_only: bool) -> Dict[str, Any]:
    try:
        return _result(fn(), validate_only)
    except GoogleAdsException as ex:
        msgs = [f"Google Ads API Error: {e.message}" for e in ex.failure.errors]
        raise ToolError(f"Request ID: {ex.request_id}\n" + "\n".join(msgs))


def _update(
    client,
    customer_id: str,
    service_name: str,
    operation_type: str,
    method: str,
    resource_name: str,
    set_fields,
    validate_only: bool,
) -> Dict[str, Any]:
    """Builds and sends a single update operation."""
    operation = client.get_type(operation_type)
    entity = operation.update
    entity.resource_name = resource_name
    set_fields(entity)
    client.copy_from(
        operation.update_mask,
        protobuf_helpers.field_mask(None, entity._pb),
    )
    service = _service(client, service_name)
    return _run(
        lambda: getattr(service, method)(
            customer_id=customer_id,
            operations=[operation],
            validate_only=validate_only,
        ),
        validate_only,
    )


# ---------------------------------------------------------------------------
# Status changes
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
def update_campaign_status(
    customer_id: str,
    campaign_id: str,
    status: Status,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Enables or pauses a campaign.

    Args:
        customer_id: Customer id, digits only.
        campaign_id: Campaign id.
        status: ENABLED or PAUSED.
        validate_only: When true (default), validate without applying.
    """
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    rn = _service(client, "CampaignService").campaign_path(
        cid, _id(campaign_id)
    )

    def set_fields(c):
        c.status = client.enums.CampaignStatusEnum[status]

    return _update(
        client,
        cid,
        "CampaignService",
        "CampaignOperation",
        "mutate_campaigns",
        rn,
        set_fields,
        validate_only,
    )


@mcp.tool(annotations=_WRITE)
def update_ad_group_status(
    customer_id: str,
    ad_group_id: str,
    status: Status,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Enables or pauses an ad group.

    Args:
        customer_id: Customer id, digits only.
        ad_group_id: Ad group id.
        status: ENABLED or PAUSED.
        validate_only: When true (default), validate without applying.
    """
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    rn = _service(client, "AdGroupService").ad_group_path(cid, _id(ad_group_id))

    def set_fields(g):
        g.status = client.enums.AdGroupStatusEnum[status]

    return _update(
        client,
        cid,
        "AdGroupService",
        "AdGroupOperation",
        "mutate_ad_groups",
        rn,
        set_fields,
        validate_only,
    )


@mcp.tool(annotations=_WRITE)
def update_ad_status(
    customer_id: str,
    ad_group_id: str,
    ad_id: str,
    status: Status,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Enables or pauses an ad (ad_group_ad).

    Args:
        customer_id: Customer id, digits only.
        ad_group_id: Ad group id containing the ad.
        ad_id: Ad id.
        status: ENABLED or PAUSED.
        validate_only: When true (default), validate without applying.
    """
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    rn = _service(client, "AdGroupAdService").ad_group_ad_path(
        cid, _id(ad_group_id), _id(ad_id)
    )

    def set_fields(a):
        a.status = client.enums.AdGroupAdStatusEnum[status]

    return _update(
        client,
        cid,
        "AdGroupAdService",
        "AdGroupAdOperation",
        "mutate_ad_group_ads",
        rn,
        set_fields,
        validate_only,
    )


@mcp.tool(annotations=_WRITE)
def update_keyword(
    customer_id: str,
    ad_group_id: str,
    criterion_id: str,
    status: Optional[Status] = None,
    cpc_bid_micros: Optional[int] = None,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Changes a keyword's status and/or max CPC bid.

    Args:
        customer_id: Customer id, digits only.
        ad_group_id: Ad group id containing the keyword.
        criterion_id: Keyword criterion id (ad_group_criterion.criterion_id).
        status: Optional ENABLED or PAUSED.
        cpc_bid_micros: Optional max CPC in micros (1 unit = 1,000,000).
        validate_only: When true (default), validate without applying.
    """
    if status is None and cpc_bid_micros is None:
        raise ToolError("Provide status and/or cpc_bid_micros.")
    if cpc_bid_micros is not None and cpc_bid_micros <= 0:
        raise ToolError("cpc_bid_micros must be positive.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    rn = _service(client, "AdGroupCriterionService").ad_group_criterion_path(
        cid, _id(ad_group_id), _id(criterion_id)
    )

    def set_fields(k):
        if status is not None:
            k.status = client.enums.AdGroupCriterionStatusEnum[status]
        if cpc_bid_micros is not None:
            k.cpc_bid_micros = cpc_bid_micros

    return _update(
        client,
        cid,
        "AdGroupCriterionService",
        "AdGroupCriterionOperation",
        "mutate_ad_group_criteria",
        rn,
        set_fields,
        validate_only,
    )


# ---------------------------------------------------------------------------
# Budgets and bids
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
def update_campaign_budget(
    customer_id: str,
    budget_id: str,
    amount_micros: int,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Sets the daily amount of a campaign budget.

    Find the budget id with the `search` tool
    (resource campaign, field campaign.campaign_budget).

    Args:
        customer_id: Customer id, digits only.
        budget_id: Campaign budget id.
        amount_micros: New daily budget in micros (1 unit = 1,000,000).
        validate_only: When true (default), validate without applying.
    """
    if amount_micros <= 0:
        raise ToolError("amount_micros must be positive.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    rn = _service(client, "CampaignBudgetService").campaign_budget_path(
        cid, _id(budget_id)
    )

    def set_fields(b):
        b.amount_micros = amount_micros

    return _update(
        client,
        cid,
        "CampaignBudgetService",
        "CampaignBudgetOperation",
        "mutate_campaign_budgets",
        rn,
        set_fields,
        validate_only,
    )


@mcp.tool(annotations=_WRITE)
def update_ad_group_bid(
    customer_id: str,
    ad_group_id: str,
    cpc_bid_micros: int,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Sets an ad group's default max CPC bid (manual CPC campaigns).

    Args:
        customer_id: Customer id, digits only.
        ad_group_id: Ad group id.
        cpc_bid_micros: Max CPC in micros (1 unit = 1,000,000).
        validate_only: When true (default), validate without applying.
    """
    if cpc_bid_micros <= 0:
        raise ToolError("cpc_bid_micros must be positive.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    rn = _service(client, "AdGroupService").ad_group_path(cid, _id(ad_group_id))

    def set_fields(g):
        g.cpc_bid_micros = cpc_bid_micros

    return _update(
        client,
        cid,
        "AdGroupService",
        "AdGroupOperation",
        "mutate_ad_groups",
        rn,
        set_fields,
        validate_only,
    )


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_CREATE)
def add_keywords(
    customer_id: str,
    ad_group_id: str,
    keywords: List[KeywordInput],
    status: Status = "PAUSED",
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Adds keywords to an ad group.

    Args:
        customer_id: Customer id, digits only.
        ad_group_id: Ad group id.
        keywords: Keywords to add, each with text, match_type and optional bid.
        status: Status of new keywords. Defaults to PAUSED.
        validate_only: When true (default), validate without applying.
    """
    if not keywords:
        raise ToolError("keywords must not be empty.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    service = _service(client, "AdGroupCriterionService")
    ad_group_rn = _service(client, "AdGroupService").ad_group_path(
        cid, _id(ad_group_id)
    )
    operations = []
    for kw in keywords:
        op = client.get_type("AdGroupCriterionOperation")
        c = op.create
        c.ad_group = ad_group_rn
        c.status = client.enums.AdGroupCriterionStatusEnum[status]
        c.keyword.text = kw.text
        c.keyword.match_type = client.enums.KeywordMatchTypeEnum[kw.match_type]
        if kw.cpc_bid_micros:
            c.cpc_bid_micros = kw.cpc_bid_micros
        operations.append(op)
    return _run(
        lambda: service.mutate_ad_group_criteria(
            customer_id=cid, operations=operations, validate_only=validate_only
        ),
        validate_only,
    )


@mcp.tool(annotations=_CREATE)
def add_negative_keywords(
    customer_id: str,
    campaign_id: str,
    keywords: List[str],
    match_type: MatchType = "PHRASE",
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Adds campaign-level negative keywords.

    Args:
        customer_id: Customer id, digits only.
        campaign_id: Campaign id.
        keywords: Negative keyword texts.
        match_type: Match type for all given negatives. Defaults to PHRASE.
        validate_only: When true (default), validate without applying.
    """
    if not keywords:
        raise ToolError("keywords must not be empty.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    service = _service(client, "CampaignCriterionService")
    campaign_rn = _service(client, "CampaignService").campaign_path(
        cid, _id(campaign_id)
    )
    operations = []
    for text in keywords:
        op = client.get_type("CampaignCriterionOperation")
        c = op.create
        c.campaign = campaign_rn
        c.negative = True
        c.keyword.text = text
        c.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]
        operations.append(op)
    return _run(
        lambda: service.mutate_campaign_criteria(
            customer_id=cid, operations=operations, validate_only=validate_only
        ),
        validate_only,
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_CREATE)
def create_search_campaign(
    customer_id: str,
    name: str,
    daily_budget_micros: int,
    status: Status = "PAUSED",
    contains_eu_political_advertising: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Creates a Search campaign with its own daily budget, using manual CPC.

    Budget and campaign are created atomically. Targets Google Search and the
    Search Network, not the Display Network.

    Args:
        customer_id: Customer id, digits only.
        name: Campaign name; must be unique in the account.
        daily_budget_micros: Daily budget in micros (1 unit = 1,000,000).
        status: Defaults to PAUSED.
        contains_eu_political_advertising: Required declaration. Defaults to false.
        start_date: Optional YYYY-MM-DD.
        end_date: Optional YYYY-MM-DD.
        validate_only: When true (default), validate without applying.
    """
    if daily_budget_micros <= 0:
        raise ToolError("daily_budget_micros must be positive.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    budget_rn = _service(client, "CampaignBudgetService").campaign_budget_path(
        cid, "-1"
    )

    budget_op = client.get_type("MutateOperation")
    b = budget_op.campaign_budget_operation.create
    b.resource_name = budget_rn
    b.name = f"{name} budget"
    b.amount_micros = daily_budget_micros
    b.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    b.explicitly_shared = False

    campaign_op = client.get_type("MutateOperation")
    c = campaign_op.campaign_operation.create
    c.name = name
    c.campaign_budget = budget_rn
    c.status = client.enums.CampaignStatusEnum[status]
    c.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    client.copy_from(c.manual_cpc, client.get_type("ManualCpc"))
    c.network_settings.target_google_search = True
    c.network_settings.target_search_network = True
    c.network_settings.target_content_network = False
    c.network_settings.target_partner_search_network = False
    eu = client.enums.EuPoliticalAdvertisingStatusEnum
    c.contains_eu_political_advertising = (
        eu.CONTAINS_EU_POLITICAL_ADVERTISING
        if contains_eu_political_advertising
        else eu.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    if start_date:
        c.start_date_time = f"{start_date} 00:00:00"
    if end_date:
        c.end_date_time = f"{end_date} 23:59:59"

    service = _service(client, "GoogleAdsService")
    return _run(
        lambda: service.mutate(
            customer_id=cid,
            mutate_operations=[budget_op, campaign_op],
            validate_only=validate_only,
        ),
        validate_only,
    )


@mcp.tool(annotations=_CREATE)
def create_ad_group(
    customer_id: str,
    campaign_id: str,
    name: str,
    cpc_bid_micros: Optional[int] = None,
    status: Status = "PAUSED",
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Creates a standard Search ad group in a campaign.

    Args:
        customer_id: Customer id, digits only.
        campaign_id: Campaign id.
        name: Ad group name; must be unique in the campaign.
        cpc_bid_micros: Optional default max CPC in micros.
        status: Defaults to PAUSED.
        validate_only: When true (default), validate without applying.
    """
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    op = client.get_type("AdGroupOperation")
    g = op.create
    g.name = name
    g.campaign = _service(client, "CampaignService").campaign_path(
        cid, _id(campaign_id)
    )
    g.status = client.enums.AdGroupStatusEnum[status]
    g.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    if cpc_bid_micros:
        g.cpc_bid_micros = cpc_bid_micros
    service = _service(client, "AdGroupService")
    return _run(
        lambda: service.mutate_ad_groups(
            customer_id=cid, operations=[op], validate_only=validate_only
        ),
        validate_only,
    )


@mcp.tool(annotations=_CREATE)
def create_responsive_search_ad(
    customer_id: str,
    ad_group_id: str,
    headlines: List[str],
    descriptions: List[str],
    final_urls: List[str],
    path1: Optional[str] = None,
    path2: Optional[str] = None,
    status: Status = "PAUSED",
    validate_only: bool = True,
) -> Dict[str, Any]:
    """Creates a responsive search ad.

    Args:
        customer_id: Customer id, digits only.
        ad_group_id: Ad group id.
        headlines: 3-15 headlines, max 30 characters each.
        descriptions: 2-4 descriptions, max 90 characters each.
        final_urls: Landing page URLs.
        path1: Optional display path 1, max 15 characters.
        path2: Optional display path 2, max 15 characters.
        status: Defaults to PAUSED.
        validate_only: When true (default), validate without applying.
    """
    if not 3 <= len(headlines) <= 15:
        raise ToolError("Provide 3-15 headlines.")
    if not 2 <= len(descriptions) <= 4:
        raise ToolError("Provide 2-4 descriptions.")
    if not final_urls:
        raise ToolError("Provide at least one final URL.")
    client = utils.get_googleads_client()
    cid = _id(customer_id)
    op = client.get_type("AdGroupAdOperation")
    a = op.create
    a.ad_group = _service(client, "AdGroupService").ad_group_path(
        cid, _id(ad_group_id)
    )
    a.status = client.enums.AdGroupAdStatusEnum[status]
    a.ad.final_urls.extend(final_urls)
    rsa = a.ad.responsive_search_ad
    for text in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = text
        rsa.headlines.append(asset)
    for text in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = text
        rsa.descriptions.append(asset)
    if path1:
        rsa.path1 = path1
    if path2:
        rsa.path2 = path2
    service = _service(client, "AdGroupAdService")
    return _run(
        lambda: service.mutate_ad_group_ads(
            customer_id=cid, operations=[op], validate_only=validate_only
        ),
        validate_only,
    )
