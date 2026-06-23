"""Channel-token and Sticker/Sticon shop endpoints."""

from __future__ import annotations

from typing import Any

from ..enums import ProductType
from ._base import ServiceMixin

# The internal Timeline/MyHome channel id the extension hard-codes.
TIMELINE_CHANNEL_ID = "1341209850"


class ChannelShopMixin(ServiceMixin):
    # -- channel token -------------------------------------------------------
    def issue_channel_token(self, channel_id: str = TIMELINE_CHANNEL_ID) -> Any:
        """``issueChannelToken(channelId)`` -> ChannelToken.

        Caches the returned ``channelAccessToken`` so subsequent requests can
        send the ``X-Line-ChannelToken`` header automatically.
        """
        data = self.transport.call("Talk.ChannelService.issueChannelToken", [channel_id])
        if isinstance(data, dict):
            tok = data.get("channelAccessToken") or data.get("token")
            if tok:
                self.transport.tokens.channel_access_token = tok
        return data

    # -- sticker / sticon shop ----------------------------------------------
    def get_owned_product_summaries(
        self,
        shop_id: str = "stickershop",
        offset: int = 0,
        limit: int = 1000,
        *,
        language: str = "en",
        country: str = "JP",
    ) -> Any:
        """``getOwnedProductSummaries(shopId, offset, limit, displayInfo)``.

        ``shop_id`` is ``"stickershop"`` or ``"sticonshop"``.
        """
        return self.transport.call(
            "ShopService.ShopService.getOwnedProductSummaries",
            [shop_id, offset, limit, {"language": language, "country": country}],
        )

    def iter_owned_products(
        self, shop_id: str = "stickershop", *, language: str = "en", country: str = "JP"
    ):
        """Generator that pages through *all* owned products."""
        offset, total = 0, None
        while total is None or offset < total:
            res = self.get_owned_product_summaries(
                shop_id, offset, 1000, language=language, country=country
            )
            if not isinstance(res, dict):
                return
            products = res.get("productList") or []
            yield from products
            total = res.get("totalSize", 0)
            if not products:
                return
            offset += len(products)

    def preview_customized_image_text(
        self, product_id: str, text: str, product_type: int = int(ProductType.STICKER)
    ) -> Any:
        """``previewCustomizedImageText(request)`` — preview a custom-name sticker."""
        return self.transport.call(
            "ShopService.ShopService.previewCustomizedImageText",
            [
                {
                    "productType": int(product_type),
                    "productId": str(product_id),
                    "nameRequestEntry": {"text": text},
                }
            ],
        )

    def set_customized_image_text(
        self, product_id: str, text: str, product_type: int = int(ProductType.STICKER)
    ) -> Any:
        """``setCustomizedImageText(request)`` — persist a custom-name sticker."""
        return self.transport.call(
            "ShopService.ShopService.setCustomizedImageText",
            [
                {
                    "productType": int(product_type),
                    "productId": str(product_id),
                    "nameRequestEntry": {"text": text},
                }
            ],
        )
