"""
Shopify Integration Adapter for LiuHao AI OS.

Provides integration with Shopify for products, variants, collections,
orders, customers, inventory, discounts, fulfillment, and webhooks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .base_adapter import (
    AuthType,
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationError,
    IntegrationStatus,
    RateLimitError,
)


class OrderStatus(str, Enum):
    """Shopify order financial statuses."""
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    VOIDED = "voided"


class FulfillmentStatus(str, Enum):
    """Shopify order fulfillment statuses."""
    FULFILLED = "fulfilled"
    PARTIAL = "partial"
    UNFULFILLED = "unfulfilled"
    RESTOCKED = "restocked"


class ProductStatus(str, Enum):
    """Shopify product statuses."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DRAFT = "draft"


class WebhookTopic(str, Enum):
    """Common Shopify webhook topics."""
    ORDERS_CREATE = "orders/create"
    ORDERS_UPDATED = "orders/updated"
    ORDERS_PAID = "orders/paid"
    ORDERS_CANCELLED = "orders/cancelled"
    ORDERS_FULFILLED = "orders/fulfilled"
    PRODUCTS_CREATE = "products/create"
    PRODUCTS_UPDATE = "products/update"
    PRODUCTS_DELETE = "products/delete"
    CUSTOMERS_CREATE = "customers/create"
    CUSTOMERS_UPDATE = "customers/update"
    CUSTOMERS_DELETE = "customers/delete"
    INVENTORY_LEVELS_UPDATE = "inventory_levels/update"
    APP_UNINSTALLED = "app/uninstalled"


@dataclass
class ShopifyProduct:
    """Represents a Shopify product."""
    id: int
    title: str
    handle: str = ""
    status: str = "active"
    body_html: str = ""
    vendor: str = ""
    product_type: str = ""
    created_at: str = ""
    updated_at: str = ""
    published_at: str = ""
    tags: List[str] = field(default_factory=list)
    variants: List[Dict[str, Any]] = field(default_factory=list)
    images: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShopifyVariant:
    """Represents a product variant."""
    id: int
    product_id: int
    title: str
    price: str = ""
    sku: str = ""
    inventory_quantity: int = 0
    inventory_item_id: int = 0
    weight: float = 0.0
    weight_unit: str = "kg"
    option1: str = ""
    option2: str = ""
    option3: str = ""
    created_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShopifyOrder:
    """Represents a Shopify order."""
    id: int
    order_number: str = ""
    name: str = ""
    email: str = ""
    phone: str = ""
    total_price: str = ""
    subtotal_price: str = ""
    total_tax: str = ""
    total_discounts: str = ""
    currency: str = ""
    financial_status: str = ""
    fulfillment_status: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    cancelled_at: str = ""
    customer: Dict[str, Any] = field(default_factory=dict)
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    shipping_address: Dict[str, Any] = field(default_factory=dict)
    billing_address: Dict[str, Any] = field(default_factory=dict)
    tags: str = ""
    note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShopifyCustomer:
    """Represents a Shopify customer."""
    id: int
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    orders_count: int = 0
    total_spent: str = ""
    currency: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_order_id: Optional[int] = None
    state: str = ""
    verified_email: bool = False
    tags: str = ""
    addresses: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShopifyCollection:
    """Represents a Shopify collection."""
    id: int
    title: str
    handle: str = ""
    body_html: str = ""
    sort_order: str = ""
    published_at: str = ""
    updated_at: str = ""
    image: Optional[Dict[str, Any]] = None
    rules: List[Dict[str, Any]] = field(default_factory=list)
    is_smart_collection: bool = False
    products_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShopifyFulfillment:
    """Represents a fulfillment."""
    id: int
    order_id: int
    status: str = ""
    tracking_number: str = ""
    tracking_company: str = ""
    tracking_url: str = ""
    created_at: str = ""
    updated_at: str = ""
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShopifyDiscountCode:
    """Represents a discount code (price rule + discount code)."""
    id: int
    price_rule_id: int
    code: str
    usage_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class ShopifyAdapter(BaseIntegrationAdapter):
    """
    Shopify integration adapter for LiuHao AI OS.

    Supports Shopify Admin REST API with:
    - Products and variants management
    - Smart and custom collections
    - Orders management (create, list, update, cancel)
    - Customers management
    - Inventory levels and locations
    - Price rules and discount codes
    - Fulfillment creation and tracking
    - Webhook registration and verification
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize Shopify adapter.

        Args:
            config: Integration config.
                    base_url: https://{shop}.myshopify.com/admin/
                    api_key / access_token: Admin API access token
                    extra["api_version"]: API version (default: 2024-01)
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        self._api_version: str = config.extra.get("api_version", "2024-01")
        if config.base_url and not config.base_url.endswith("/"):
            config.base_url += "/"
        super().__init__(config)
        self._shop_name: Optional[str] = None
        self._shop_email: Optional[str] = None
        self._shop_domain: Optional[str] = None

    @property
    def _api_base(self) -> str:
        """Base URL for API calls."""
        return f"{self.config.base_url}api/{self._api_version}/"

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to Shopify and validate credentials."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Shopify API...")

        try:
            shop_data = self._api_call("GET", "shop.json")
            shop = shop_data.get("shop", {})
            self._shop_name = shop.get("name")
            self._shop_email = shop.get("email")
            self._shop_domain = shop.get("myshopify_domain")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Shopify store: %s", self._shop_name,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Shopify."""
        self._status = ConnectionStatus.DISCONNECTED
        self._shop_name = None
        self._shop_email = None
        self._shop_domain = None
        self._session = None
        self._logger.info("Disconnected from Shopify")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "shop_name": self._shop_name,
            "shop_domain": self._shop_domain,
            "api_version": self._api_version,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "shop.json")
            return "shop" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def list_products(self,
                      limit: int = 50,
                      page_info: Optional[str] = None,
                      status: Optional[ProductStatus] = None,
                      collection_id: Optional[int] = None,
                      created_at_min: Optional[str] = None,
                      updated_at_min: Optional[str] = None,
                      title: Optional[str] = None,
                      vendor: Optional[str] = None,
                      product_type: Optional[str] = None,
                      ) -> Tuple[List[ShopifyProduct], Optional[str]]:
        """
        List products with optional filters.

        Returns:
            Tuple of (products list, next_page_info or None).
        """
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if page_info:
            params["page_info"] = page_info
        if status:
            params["status"] = status.value
        if collection_id:
            params["collection_id"] = collection_id
        if created_at_min:
            params["created_at_min"] = created_at_min
        if updated_at_min:
            params["updated_at_min"] = updated_at_min
        if title:
            params["title"] = title
        if vendor:
            params["vendor"] = vendor
        if product_type:
            params["product_type"] = product_type

        data, next_page = self._api_call_with_pagination(
            "GET", "products.json", params=params,
        )
        products = [self._parse_product(p) for p in data.get("products", [])]
        return products, next_page

    def get_product(self, product_id: int) -> ShopifyProduct:
        """Get a product by ID."""
        data = self._api_call("GET", f"products/{product_id}.json")
        return self._parse_product(data.get("product", {}))

    def create_product(self, title: str,
                       body_html: str = "",
                       vendor: str = "",
                       product_type: str = "",
                       status: str = "draft",
                       tags: Optional[List[str]] = None,
                       variants: Optional[List[Dict[str, Any]]] = None,
                       images: Optional[List[Dict[str, Any]]] = None
                       ) -> ShopifyProduct:
        """Create a new product."""
        product: Dict[str, Any] = {
            "title": title,
            "body_html": body_html,
            "vendor": vendor,
            "product_type": product_type,
            "status": status,
        }
        if tags:
            product["tags"] = ", ".join(tags)
        if variants:
            product["variants"] = variants
        if images:
            product["images"] = images

        data = self._api_call(
            "POST", "products.json", json_body={"product": product},
        )
        return self._parse_product(data.get("product", {}))

    def update_product(self, product_id: int,
                       **kwargs: Any) -> ShopifyProduct:
        """Update a product."""
        data = self._api_call(
            "PUT", f"products/{product_id}.json",
            json_body={"product": kwargs},
        )
        return self._parse_product(data.get("product", {}))

    def delete_product(self, product_id: int) -> bool:
        """Delete a product. Returns True on success."""
        try:
            self._api_call("DELETE", f"products/{product_id}.json")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Variants
    # ------------------------------------------------------------------

    def list_variants(self, product_id: int,
                      limit: int = 50,
                      page_info: Optional[str] = None
                      ) -> Tuple[List[ShopifyVariant], Optional[str]]:
        """List variants for a product."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if page_info:
            params["page_info"] = page_info

        data, next_page = self._api_call_with_pagination(
            "GET", f"products/{product_id}/variants.json", params=params,
        )
        variants = [self._parse_variant(v, product_id)
                    for v in data.get("variants", [])]
        return variants, next_page

    def get_variant(self, variant_id: int) -> ShopifyVariant:
        """Get a variant by ID."""
        data = self._api_call("GET", f"variants/{variant_id}.json")
        v = data.get("variant", {})
        return self._parse_variant(v, v.get("product_id", 0))

    def create_variant(self, product_id: int,
                       option1: str,
                       price: str = "",
                       sku: str = "",
                       inventory_quantity: int = 0,
                       **kwargs: Any) -> ShopifyVariant:
        """Create a new product variant."""
        variant = {
            "option1": option1,
            "price": price,
            "sku": sku,
            "inventory_quantity": inventory_quantity,
            **kwargs,
        }
        data = self._api_call(
            "POST", f"products/{product_id}/variants.json",
            json_body={"variant": variant},
        )
        v = data.get("variant", {})
        return self._parse_variant(v, product_id)

    def update_variant(self, variant_id: int, **kwargs: Any) -> ShopifyVariant:
        """Update a variant."""
        data = self._api_call(
            "PUT", f"variants/{variant_id}.json",
            json_body={"variant": kwargs},
        )
        v = data.get("variant", {})
        return self._parse_variant(v, v.get("product_id", 0))

    def delete_variant(self, product_id: int, variant_id: int) -> bool:
        """Delete a variant. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"products/{product_id}/variants/{variant_id}.json",
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def list_collections(self, limit: int = 50,
                         page_info: Optional[str] = None,
                         title: Optional[str] = None,
                         product_id: Optional[int] = None,
                         ) -> Tuple[List[ShopifyCollection], Optional[str]]:
        """List custom collections."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if page_info:
            params["page_info"] = page_info
        if title:
            params["title"] = title
        if product_id:
            params["product_id"] = product_id

        data, next_page = self._api_call_with_pagination(
            "GET", "custom_collections.json", params=params,
        )
        collections = [self._parse_collection(c, is_smart=False)
                       for c in data.get("custom_collections", [])]
        return collections, next_page

    def list_smart_collections(self, limit: int = 50,
                               page_info: Optional[str] = None,
                               title: Optional[str] = None,
                               ) -> Tuple[List[ShopifyCollection], Optional[str]]:
        """List smart collections."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if page_info:
            params["page_info"] = page_info
        if title:
            params["title"] = title

        data, next_page = self._api_call_with_pagination(
            "GET", "smart_collections.json", params=params,
        )
        collections = [self._parse_collection(c, is_smart=True)
                       for c in data.get("smart_collections", [])]
        return collections, next_page

    def get_collection(self, collection_id: int,
                       is_smart: bool = False) -> ShopifyCollection:
        """Get a collection by ID."""
        endpoint = "smart_collections" if is_smart else "custom_collections"
        data = self._api_call("GET", f"{endpoint}/{collection_id}.json")
        key = "smart_collection" if is_smart else "custom_collection"
        return self._parse_collection(data.get(key, {}), is_smart=is_smart)

    def create_collection(self, title: str,
                          body_html: str = "",
                          image: Optional[Dict[str, Any]] = None
                          ) -> ShopifyCollection:
        """Create a custom collection."""
        collection: Dict[str, Any] = {
            "title": title,
            "body_html": body_html,
        }
        if image:
            collection["image"] = image

        data = self._api_call(
            "POST", "custom_collections.json",
            json_body={"custom_collection": collection},
        )
        return self._parse_collection(data.get("custom_collection", {}))

    def add_product_to_collection(self, collection_id: int,
                                  product_id: int) -> bool:
        """Add a product to a custom collection."""
        try:
            self._api_call(
                "PUT", "collects.json",
                json_body={"collect": {
                    "collection_id": collection_id,
                    "product_id": product_id,
                }},
            )
            return True
        except Exception:
            return False

    def remove_product_from_collection(self, collect_id: int) -> bool:
        """Remove a product from a collection (by collect ID)."""
        try:
            self._api_call("DELETE", f"collects/{collect_id}.json")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def list_orders(self,
                    limit: int = 50,
                    page_info: Optional[str] = None,
                    status: str = "any",
                    financial_status: Optional[OrderStatus] = None,
                    fulfillment_status: Optional[FulfillmentStatus] = None,
                    created_at_min: Optional[str] = None,
                    created_at_max: Optional[str] = None,
                    email: Optional[str] = None,
                    ) -> Tuple[List[ShopifyOrder], Optional[str]]:
        """
        List orders with filters.

        Args:
            status: "open", "closed", "cancelled", "any".
            financial_status: Filter by payment status.
            fulfillment_status: Filter by fulfillment status.
        """
        params: Dict[str, Any] = {"limit": min(limit, 250), "status": status}
        if page_info:
            params["page_info"] = page_info
        if financial_status:
            params["financial_status"] = financial_status.value
        if fulfillment_status:
            params["fulfillment_status"] = fulfillment_status.value
        if created_at_min:
            params["created_at_min"] = created_at_min
        if created_at_max:
            params["created_at_max"] = created_at_max
        if email:
            params["email"] = email

        data, next_page = self._api_call_with_pagination(
            "GET", "orders.json", params=params,
        )
        orders = [self._parse_order(o) for o in data.get("orders", [])]
        return orders, next_page

    def get_order(self, order_id: int) -> ShopifyOrder:
        """Get an order by ID."""
        data = self._api_call("GET", f"orders/{order_id}.json")
        return self._parse_order(data.get("order", {}))

    def create_order(self, line_items: List[Dict[str, Any]],
                     email: str = "",
                     financial_status: str = "pending",
                     tags: str = "",
                     note: str = "",
                     customer: Optional[Dict[str, Any]] = None,
                     shipping_address: Optional[Dict[str, Any]] = None,
                     billing_address: Optional[Dict[str, Any]] = None,
                     **kwargs: Any) -> ShopifyOrder:
        """Create a new order."""
        order: Dict[str, Any] = {
            "line_items": line_items,
            "email": email,
            "financial_status": financial_status,
            "tags": tags,
            "note": note,
            **kwargs,
        }
        if customer:
            order["customer"] = customer
        if shipping_address:
            order["shipping_address"] = shipping_address
        if billing_address:
            order["billing_address"] = billing_address

        data = self._api_call(
            "POST", "orders.json", json_body={"order": order},
        )
        return self._parse_order(data.get("order", {}))

    def update_order(self, order_id: int, **kwargs: Any) -> ShopifyOrder:
        """Update an order."""
        data = self._api_call(
            "PUT", f"orders/{order_id}.json",
            json_body={"order": kwargs},
        )
        return self._parse_order(data.get("order", {}))

    def cancel_order(self, order_id: int,
                     reason: str = "other",
                     restock: bool = False,
                     refund: bool = False) -> ShopifyOrder:
        """Cancel an order."""
        payload: Dict[str, Any] = {
            "reason": reason,
            "restock": restock,
            "refund": refund,
        }
        data = self._api_call(
            "POST", f"orders/{order_id}/cancel.json",
            json_body=payload,
        )
        return self._parse_order(data.get("order", {}))

    def get_order_count(self,
                        status: str = "any",
                        financial_status: Optional[OrderStatus] = None,
                        ) -> int:
        """Get total count of orders matching filters."""
        params: Dict[str, Any] = {"status": status}
        if financial_status:
            params["financial_status"] = financial_status.value

        data = self._api_call("GET", "orders/count.json", params=params)
        return data.get("count", 0)

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    def list_customers(self, limit: int = 50,
                       page_info: Optional[str] = None,
                       email: Optional[str] = None,
                       created_at_min: Optional[str] = None,
                       updated_at_min: Optional[str] = None,
                       ) -> Tuple[List[ShopifyCustomer], Optional[str]]:
        """List customers."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if page_info:
            params["page_info"] = page_info
        if email:
            params["email"] = email
        if created_at_min:
            params["created_at_min"] = created_at_min
        if updated_at_min:
            params["updated_at_min"] = updated_at_min

        data, next_page = self._api_call_with_pagination(
            "GET", "customers.json", params=params,
        )
        customers = [self._parse_customer(c) for c in data.get("customers", [])]
        return customers, next_page

    def get_customer(self, customer_id: int) -> ShopifyCustomer:
        """Get a customer by ID."""
        data = self._api_call("GET", f"customers/{customer_id}.json")
        return self._parse_customer(data.get("customer", {}))

    def create_customer(self, email: str,
                        first_name: str = "",
                        last_name: str = "",
                        phone: str = "",
                        tags: str = "",
                        addresses: Optional[List[Dict[str, Any]]] = None,
                        send_email_invite: bool = False,
                        **kwargs: Any) -> ShopifyCustomer:
        """Create a new customer."""
        customer: Dict[str, Any] = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "tags": tags,
            **kwargs,
        }
        if addresses:
            customer["addresses"] = addresses

        params = {}
        if send_email_invite:
            params["send_email_welcome"] = "true"

        data = self._api_call(
            "POST", "customers.json",
            json_body={"customer": customer},
            params=params if params else None,
        )
        return self._parse_customer(data.get("customer", {}))

    def update_customer(self, customer_id: int, **kwargs: Any) -> ShopifyCustomer:
        """Update a customer."""
        data = self._api_call(
            "PUT", f"customers/{customer_id}.json",
            json_body={"customer": kwargs},
        )
        return self._parse_customer(data.get("customer", {}))

    def search_customers(self, query: str,
                         limit: int = 50) -> List[ShopifyCustomer]:
        """Search customers by name, email, etc."""
        data = self._api_call(
            "GET", "customers/search.json",
            params={"query": query, "limit": min(limit, 250)},
        )
        return [self._parse_customer(c) for c in data.get("customers", [])]

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    def get_inventory_levels(self,
                             inventory_item_ids: Optional[List[int]] = None,
                             location_ids: Optional[List[int]] = None,
                             limit: int = 50,
                             page_info: Optional[str] = None
                             ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get inventory levels."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if inventory_item_ids:
            params["inventory_item_ids"] = ",".join(
                str(i) for i in inventory_item_ids
            )
        if location_ids:
            params["location_ids"] = ",".join(
                str(l) for l in location_ids
            )
        if page_info:
            params["page_info"] = page_info

        data, next_page = self._api_call_with_pagination(
            "GET", "inventory_levels.json", params=params,
        )
        return data.get("inventory_levels", []), next_page

    def set_inventory_level(self, inventory_item_id: int,
                            location_id: int,
                            available: int,
                            disconnect_if_necessary: bool = False) -> Dict[str, Any]:
        """Set inventory level for an item at a location."""
        payload = {
            "inventory_item_id": inventory_item_id,
            "location_id": location_id,
            "available": available,
            "disconnect_if_necessary": disconnect_if_necessary,
        }
        data = self._api_call(
            "POST", "inventory_levels/set.json", json_body=payload,
        )
        return data.get("inventory_level", {})

    def adjust_inventory(self, inventory_item_id: int,
                         location_id: int,
                         available_adjustment: int) -> Dict[str, Any]:
        """Adjust inventory level (relative change)."""
        payload = {
            "inventory_item_id": inventory_item_id,
            "location_id": location_id,
            "available_adjustment": available_adjustment,
        }
        data = self._api_call(
            "POST", "inventory_levels/adjust.json", json_body=payload,
        )
        return data.get("inventory_level", {})

    def list_locations(self) -> List[Dict[str, Any]]:
        """List all inventory locations."""
        data = self._api_call("GET", "locations.json")
        return data.get("locations", [])

    # ------------------------------------------------------------------
    # Discounts
    # ------------------------------------------------------------------

    def list_price_rules(self, limit: int = 50,
                         page_info: Optional[str] = None
                         ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """List price rules."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if page_info:
            params["page_info"] = page_info

        data, next_page = self._api_call_with_pagination(
            "GET", "price_rules.json", params=params,
        )
        return data.get("price_rules", []), next_page

    def create_price_rule(self, title: str,
                          target_type: str,
                          target_selection: str,
                          allocation_method: str,
                          value_type: str,
                          value: str,
                          customer_selection: str = "all",
                          once_per_customer: bool = False,
                          starts_at: Optional[str] = None,
                          ends_at: Optional[str] = None,
                          **kwargs: Any) -> Dict[str, Any]:
        """Create a price rule."""
        price_rule: Dict[str, Any] = {
            "title": title,
            "target_type": target_type,
            "target_selection": target_selection,
            "allocation_method": allocation_method,
            "value_type": value_type,
            "value": value,
            "customer_selection": customer_selection,
            "once_per_customer": once_per_customer,
            "starts_at": starts_at or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime()),
            **kwargs,
        }
        if ends_at:
            price_rule["ends_at"] = ends_at

        data = self._api_call(
            "POST", "price_rules.json",
            json_body={"price_rule": price_rule},
        )
        return data.get("price_rule", {})

    def create_discount_code(self, price_rule_id: int,
                             code: str,
                             usage_count: int = 0) -> ShopifyDiscountCode:
        """Create a discount code for a price rule."""
        discount = {"code": code, "usage_count": usage_count}
        data = self._api_call(
            "POST", f"price_rules/{price_rule_id}/discount_codes.json",
            json_body={"discount_code": discount},
        )
        dc = data.get("discount_code", {})
        return ShopifyDiscountCode(
            id=dc.get("id", 0),
            price_rule_id=price_rule_id,
            code=dc.get("code", ""),
            usage_count=dc.get("usage_count", 0),
            created_at=dc.get("created_at", ""),
            updated_at=dc.get("updated_at", ""),
            extra=dc,
        )

    # ------------------------------------------------------------------
    # Fulfillment
    # ------------------------------------------------------------------

    def list_fulfillments(self, order_id: int) -> List[ShopifyFulfillment]:
        """List fulfillments for an order."""
        data = self._api_call("GET", f"orders/{order_id}/fulfillments.json")
        return [self._parse_fulfillment(f, order_id)
                for f in data.get("fulfillments", [])]

    def create_fulfillment(self, order_id: int,
                           line_items_by_fulfillment_order: List[Dict[str, Any]],
                           tracking_number: str = "",
                           tracking_company: str = "",
                           tracking_url: str = "",
                           notify_customer: bool = False,
                           ) -> ShopifyFulfillment:
        """Create a fulfillment for an order."""
        fulfillment: Dict[str, Any] = {
            "line_items_by_fulfillment_order": line_items_by_fulfillment_order,
            "notify_customer": notify_customer,
        }
        if tracking_number:
            fulfillment["tracking_info"] = {
                "number": tracking_number,
                "company": tracking_company,
                "url": tracking_url,
            }

        data = self._api_call(
            "POST", "fulfillments.json",
            json_body={"fulfillment": fulfillment},
        )
        f = data.get("fulfillment", {})
        return self._parse_fulfillment(f, f.get("order_id", order_id))

    def update_fulfillment_tracking(self, fulfillment_id: int,
                                    tracking_number: str = "",
                                    tracking_company: str = "",
                                    tracking_url: str = ""
                                    ) -> ShopifyFulfillment:
        """Update tracking info on a fulfillment."""
        fulfillment: Dict[str, Any] = {
            "tracking_info": {
                "number": tracking_number,
                "company": tracking_company,
                "url": tracking_url,
            },
        }
        data = self._api_call(
            "PUT", f"fulfillments/{fulfillment_id}.json",
            json_body={"fulfillment": fulfillment},
        )
        f = data.get("fulfillment", {})
        return self._parse_fulfillment(f, f.get("order_id", 0))

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def register_webhook(self, topic: str, address: str,
                         format_: str = "json") -> Dict[str, Any]:
        """
        Register a webhook.

        Args:
            topic: Webhook topic (e.g., "orders/create").
            address: Callback URL.
            format_: Payload format ("json" or "xml").

        Returns:
            Created webhook data.
        """
        webhook = {"topic": topic, "address": address, "format": format_}
        data = self._api_call(
            "POST", "webhooks.json", json_body={"webhook": webhook},
        )
        return data.get("webhook", {})

    def list_webhooks(self) -> List[Dict[str, Any]]:
        """List all registered webhooks."""
        data = self._api_call("GET", "webhooks.json")
        return data.get("webhooks", [])

    def delete_webhook(self, webhook_id: int) -> bool:
        """Delete a webhook. Returns True on success."""
        try:
            self._api_call("DELETE", f"webhooks/{webhook_id}.json")
            return True
        except Exception:
            return False

    def verify_webhook(self, body: bytes, hmac_header: str) -> bool:
        """
        Verify a Shopify webhook request using HMAC-SHA256.

        Args:
            body: Raw request body bytes.
            hmac_header: X-Shopify-Hmac-Sha256 header value (base64).

        Returns:
            True if signature is valid.
        """
        if not self.config.webhook_secret:
            return False

        import base64
        computed = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        computed_b64 = base64.b64encode(computed).decode("utf-8")
        return hmac.compare_digest(computed_b64, hmac_header)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_product(self, data: Dict[str, Any]) -> ShopifyProduct:
        tags_str = data.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        return ShopifyProduct(
            id=data.get("id", 0),
            title=data.get("title", ""),
            handle=data.get("handle", ""),
            status=data.get("status", "active"),
            body_html=data.get("body_html", "") or "",
            vendor=data.get("vendor", ""),
            product_type=data.get("product_type", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            published_at=data.get("published_at", "") or "",
            tags=tags,
            variants=data.get("variants", []) or [],
            images=data.get("images", []) or [],
            extra=data,
        )

    def _parse_variant(self, data: Dict[str, Any],
                       product_id: int) -> ShopifyVariant:
        return ShopifyVariant(
            id=data.get("id", 0),
            product_id=data.get("product_id", product_id),
            title=data.get("title", ""),
            price=data.get("price", ""),
            sku=data.get("sku", "") or "",
            inventory_quantity=data.get("inventory_quantity", 0) or 0,
            inventory_item_id=data.get("inventory_item_id", 0) or 0,
            weight=float(data.get("weight", 0) or 0),
            weight_unit=data.get("weight_unit", "kg"),
            option1=data.get("option1", "") or "",
            option2=data.get("option2", "") or "",
            option3=data.get("option3", "") or "",
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            extra=data,
        )

    def _parse_order(self, data: Dict[str, Any]) -> ShopifyOrder:
        return ShopifyOrder(
            id=data.get("id", 0),
            order_number=str(data.get("order_number", "")),
            name=data.get("name", ""),
            email=data.get("email", "") or "",
            phone=data.get("phone", "") or "",
            total_price=data.get("total_price", ""),
            subtotal_price=data.get("subtotal_price", ""),
            total_tax=data.get("total_tax", ""),
            total_discounts=data.get("total_discounts", ""),
            currency=data.get("currency", ""),
            financial_status=data.get("financial_status", ""),
            fulfillment_status=data.get("fulfillment_status"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            cancelled_at=data.get("cancelled_at", "") or "",
            customer=data.get("customer", {}) or {},
            line_items=data.get("line_items", []) or [],
            shipping_address=data.get("shipping_address", {}) or {},
            billing_address=data.get("billing_address", {}) or {},
            tags=data.get("tags", "") or "",
            note=data.get("note", "") or "",
            extra=data,
        )

    def _parse_customer(self, data: Dict[str, Any]) -> ShopifyCustomer:
        return ShopifyCustomer(
            id=data.get("id", 0),
            email=data.get("email", "") or "",
            first_name=data.get("first_name", "") or "",
            last_name=data.get("last_name", "") or "",
            phone=data.get("phone", "") or "",
            orders_count=data.get("orders_count", 0) or 0,
            total_spent=data.get("total_spent", ""),
            currency=data.get("currency", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_order_id=data.get("last_order_id"),
            state=data.get("state", ""),
            verified_email=data.get("verified_email", False),
            tags=data.get("tags", "") or "",
            addresses=data.get("addresses", []) or [],
            extra=data,
        )

    def _parse_collection(self, data: Dict[str, Any],
                          is_smart: bool = False) -> ShopifyCollection:
        return ShopifyCollection(
            id=data.get("id", 0),
            title=data.get("title", ""),
            handle=data.get("handle", ""),
            body_html=data.get("body_html", "") or "",
            sort_order=data.get("sort_order", ""),
            published_at=data.get("published_at", "") or "",
            updated_at=data.get("updated_at", ""),
            image=data.get("image"),
            rules=data.get("rules", []) or [],
            is_smart_collection=is_smart,
            products_count=data.get("products_count", 0) or 0,
            extra=data,
        )

    def _parse_fulfillment(self, data: Dict[str, Any],
                           order_id: int) -> ShopifyFulfillment:
        return ShopifyFulfillment(
            id=data.get("id", 0),
            order_id=data.get("order_id", order_id),
            status=data.get("status", ""),
            tracking_number=data.get("tracking_number", "") or "",
            tracking_company=data.get("tracking_company", "") or "",
            tracking_url=data.get("tracking_url", "") or "",
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            line_items=data.get("line_items", []) or [],
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helpers
    # ------------------------------------------------------------------

    def _api_call_with_pagination(self, method: str, path: str,
                                  params: Optional[Dict[str, Any]] = None
                                  ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Make an API call and extract pagination info from Link header.

        Returns:
            Tuple of (response_data, next_page_info or None).
        """
        # We'll make the call and capture headers
        url = f"{self._api_base}{path}"
        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LiuHao-AI-OS",
        }

        self._check_rate_limit()
        self._mark_request()

        from urllib.parse import urlencode
        full_url = f"{url}?{urlencode(params or {})}"

        self._log_request(method, full_url, headers, None)

        try:
            import urllib.request
            req = urllib.request.Request(
                full_url, headers=headers, method=method,
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response(method, full_url, resp.status, resp_body)

                # Parse Link header for pagination
                next_page = self._parse_link_header(
                    resp.headers.get("Link", "")
                )

                return json.loads(resp_body), next_page

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, full_url, e.code, resp_body)
            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 1))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"Shopify rate limited, retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )
            raise self._handle_error(
                f"Shopify API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )

    def _parse_link_header(self, link_header: str) -> Optional[str]:
        """Parse Shopify's Link header to get next page_info."""
        if not link_header:
            return None

        # Link header format: <url>; rel="next", <url>; rel="previous"
        parts = link_header.split(",")
        for part in parts:
            if 'rel="next"' in part:
                # Extract URL and find page_info param
                url_part = part.strip().split(";")[0].strip().strip("<>")
                if "page_info=" in url_part:
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_part)
                    params = parse_qs(parsed.query)
                    if "page_info" in params:
                        return params["page_info"][0]
        return None

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a Shopify API call with retry on rate limits."""
        try:
            return self._make_request(method, path, params, json_body)
        except RateLimitError as e:
            if _retry_count < self.config.max_retries:
                wait_time = min(e.retry_after, 60)
                if wait_time <= 0:
                    wait_time = 2 ** _retry_count
                self._logger.info(
                    "Rate limited, retrying in %.1fs (attempt %d/%d)",
                    wait_time, _retry_count + 1, self.config.max_retries,
                )
                time.sleep(wait_time)
                return self._api_call(
                    method, path, params, json_body,
                    _retry_count=_retry_count + 1,
                )
            raise

    def _make_request(self, method: str, path: str,
                      params: Optional[Dict[str, Any]],
                      json_body: Optional[Dict[str, Any]]) -> Any:
        """Execute a single Shopify API request."""
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._api_base}{path}"
        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LiuHao-AI-OS",
        }

        body_str = json.dumps(json_body) if json_body else ""
        self._log_request(method, url, headers, body_str or params)

        try:
            import urllib.request
            from urllib.parse import urlencode

            full_url = url
            if params and method == "GET":
                full_url = f"{url}?{urlencode(params)}"

            data = body_str.encode("utf-8") if body_str and method != "GET" else None
            req = urllib.request.Request(
                full_url, data=data, headers=headers, method=method,
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response(method, url, resp.status, resp_body)

                if resp.status == 204:
                    return {}
                if not resp_body:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 1))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"Shopify rate limited, retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )

            if e.code in (401, 403):
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    f"Authentication failed (HTTP {e.code})",
                    status_code=e.code,
                    response_body=resp_body,
                )

            # Parse Shopify errors
            error_msg = f"Shopify API error {e.code}"
            try:
                error_data = json.loads(resp_body)
                if "errors" in error_data:
                    errors = error_data["errors"]
                    if isinstance(errors, dict):
                        error_msg = "; ".join(
                            f"{k}: {v}" for k, v in errors.items()
                        )
                    elif isinstance(errors, str):
                        error_msg = errors
            except Exception:
                pass

            raise self._handle_error(
                error_msg,
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
