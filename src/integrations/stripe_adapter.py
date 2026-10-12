"""
Stripe Integration Adapter for LiuHao AI OS.

Provides integration with Stripe payment API for customers, charges,
subscriptions, invoices, products and prices, payment intents, refunds,
and webhook events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .base_adapter import (
    AuthType,
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationError,
    IntegrationStatus,
    RateLimitError,
)


class StripeCurrency(str, Enum):
    """Common currency codes."""
    USD = "usd"
    EUR = "eur"
    GBP = "gbp"
    JPY = "jpy"
    CNY = "cny"
    CAD = "cad"
    AUD = "aud"
    SGD = "sgd"
    HKD = "hkd"
    INR = "inr"


class StripePaymentIntentStatus(str, Enum):
    """Payment intent status values."""
    REQUIRES_PAYMENT_METHOD = "requires_payment_method"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    REQUIRES_CAPTURE = "requires_capture"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"


class StripeSubscriptionStatus(str, Enum):
    """Subscription status values."""
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


class StripeInvoiceStatus(str, Enum):
    """Invoice status values."""
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    UNCOLLECTIBLE = "uncollectible"
    VOID = "void"


class StripeRefundStatus(str, Enum):
    """Refund status values."""
    PENDING = "pending"
    REQUIRES_ACTION = "requires_action"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class StripeCustomer:
    """Represents a Stripe customer."""
    id: str = ""
    object: str = "customer"
    email: str = ""
    name: str = ""
    description: str = ""
    phone: str = ""
    address: Dict[str, Any] = field(default_factory=dict)
    balance: int = 0
    currency: str = ""
    created: int = 0
    default_source: str = ""
    delinquent: bool = False
    discount: Optional[Dict[str, Any]] = None
    invoice_prefix: str = ""
    invoice_settings: Dict[str, Any] = field(default_factory=dict)
    livemode: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)
    preferred_locales: List[str] = field(default_factory=list)
    shipping: Optional[Dict[str, Any]] = None
    tax_exempt: str = "none"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripeCharge:
    """Represents a Stripe charge."""
    id: str = ""
    object: str = "charge"
    amount: int = 0
    amount_captured: int = 0
    amount_refunded: int = 0
    application: str = ""
    application_fee: str = ""
    balance_transaction: str = ""
    captured: bool = False
    currency: StripeCurrency = StripeCurrency.USD
    customer: str = ""
    description: str = ""
    disputed: bool = False
    failure_code: str = ""
    failure_message: str = ""
    fraud_details: Dict[str, Any] = field(default_factory=dict)
    invoice: str = ""
    livemode: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)
    paid: bool = False
    payment_intent: str = ""
    payment_method: str = ""
    receipt_email: str = ""
    receipt_number: str = ""
    receipt_url: str = ""
    refunded: bool = False
    shipping: Optional[Dict[str, Any]] = None
    statement_descriptor: str = ""
    status: str = ""
    created: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripeSubscription:
    """Represents a Stripe subscription."""
    id: str = ""
    object: str = "subscription"
    customer: str = ""
    status: StripeSubscriptionStatus = StripeSubscriptionStatus.INCOMPLETE
    current_period_start: int = 0
    current_period_end: int = 0
    start_date: int = 0
    ended_at: Optional[int] = None
    trial_start: Optional[int] = None
    trial_end: Optional[int] = None
    cancel_at: Optional[int] = None
    canceled_at: Optional[int] = None
    cancel_at_period_end: bool = False
    items: List[Dict[str, Any]] = field(default_factory=list)
    latest_invoice: str = ""
    pending_invoice_item_interval: Optional[Dict[str, Any]] = None
    default_payment_method: str = ""
    default_source: str = ""
    default_tax_rates: List[Dict[str, Any]] = field(default_factory=list)
    discount: Optional[Dict[str, Any]] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    pause_collection: Optional[Dict[str, Any]] = None
    payment_settings: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripeInvoice:
    """Represents a Stripe invoice."""
    id: str = ""
    object: str = "invoice"
    customer: str = ""
    subscription: str = ""
    status: StripeInvoiceStatus = StripeInvoiceStatus.DRAFT
    amount_due: int = 0
    amount_paid: int = 0
    amount_remaining: int = 0
    attempt_count: int = 0
    attempted: bool = False
    auto_advance: bool = True
    billing_reason: str = ""
    charge: str = ""
    currency: StripeCurrency = StripeCurrency.USD
    description: str = ""
    due_date: Optional[int] = None
    ending_balance: int = 0
    hosted_invoice_url: str = ""
    invoice_pdf: str = ""
    lines: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    number: str = ""
    paid: bool = False
    receipt_number: str = ""
    starting_balance: int = 0
    statement_descriptor: str = ""
    subtotal: int = 0
    tax: int = 0
    total: int = 0
    webhooks_delivered_at: Optional[int] = None
    created: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripeProduct:
    """Represents a Stripe product."""
    id: str = ""
    object: str = "product"
    name: str = ""
    description: str = ""
    active: bool = True
    created: int = 0
    images: List[str] = field(default_factory=list)
    livemode: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)
    package_dimensions: Optional[Dict[str, Any]] = None
    shippable: Optional[bool] = None
    statement_descriptor: str = ""
    tax_code: str = ""
    unit_label: str = ""
    updated: int = 0
    url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripePrice:
    """Represents a Stripe price."""
    id: str = ""
    object: str = "price"
    product: str = ""
    unit_amount: int = 0
    currency: StripeCurrency = StripeCurrency.USD
    active: bool = True
    billing_scheme: str = "per_unit"
    created: int = 0
    livemode: bool = False
    lookup_key: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)
    nickname: str = ""
    recurring: Optional[Dict[str, Any]] = None
    tax_behavior: str = ""
    tiers: Optional[List[Dict[str, Any]]] = None
    tiers_mode: str = ""
    transform_quantity: Optional[Dict[str, Any]] = None
    type: str = "one_time"
    unit_amount_decimal: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripePaymentIntent:
    """Represents a Stripe payment intent."""
    id: str = ""
    object: str = "payment_intent"
    amount: int = 0
    amount_capturable: int = 0
    amount_details: Dict[str, Any] = field(default_factory=dict)
    amount_received: int = 0
    application: str = ""
    application_fee_amount: Optional[int] = None
    automatic_payment_methods: Optional[Dict[str, Any]] = None
    canceled_at: Optional[int] = None
    cancellation_reason: str = ""
    capture_method: str = "automatic"
    client_secret: str = ""
    confirmation_method: str = "automatic"
    created: int = 0
    currency: StripeCurrency = StripeCurrency.USD
    customer: str = ""
    description: str = ""
    invoice: str = ""
    latest_charge: str = ""
    livemode: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)
    next_action: Optional[Dict[str, Any]] = None
    on_behalf_of: str = ""
    payment_method: str = ""
    payment_method_types: List[str] = field(default_factory=list)
    processing: Optional[Dict[str, Any]] = None
    receipt_email: str = ""
    review: str = ""
    setup_future_usage: str = ""
    shipping: Optional[Dict[str, Any]] = None
    statement_descriptor: str = ""
    statement_descriptor_suffix: str = ""
    status: StripePaymentIntentStatus = StripePaymentIntentStatus.REQUIRES_PAYMENT_METHOD
    transfer_data: Optional[Dict[str, Any]] = None
    transfer_group: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StripeRefund:
    """Represents a Stripe refund."""
    id: str = ""
    object: str = "refund"
    amount: int = 0
    balance_transaction: str = ""
    charge: str = ""
    created: int = 0
    currency: StripeCurrency = StripeCurrency.USD
    description: str = ""
    failure_balance_transaction: str = ""
    failure_reason: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)
    next_action: Optional[Dict[str, Any]] = None
    payment_intent: str = ""
    reason: str = ""
    receipt_number: str = ""
    source_transfer_reversal: str = ""
    status: StripeRefundStatus = StripeRefundStatus.PENDING
    transfer_reversal: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class StripeAdapter(BaseIntegrationAdapter):
    """
    Stripe integration adapter for LiuHao AI OS.

    Supports:
    - Customers (CRUD)
    - Charges and payments
    - Subscriptions
    - Invoices
    - Products and prices
    - Payment intents
    - Refunds
    - Webhook event verification
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.stripe.com/v1"
        super().__init__(config)
        self._account_id: Optional[str] = None
        self._account_email: Optional[str] = None
        self._livemode: bool = False
        self._cached_customers: Dict[str, StripeCustomer] = {}
        self._cached_products: Dict[str, StripeProduct] = {}
        self._webhook_secret: str = config.webhook_secret or ""

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Stripe API...")

        try:
            result = self._api_call("account")
            self._account_id = result.get("id", "")
            self._account_email = result.get("email", "")
            self._livemode = result.get("livemode", False)
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Stripe (account: %s, livemode: %s)",
                self._account_id, self._livemode,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        self._account_id = None
        self._account_email = None
        self._livemode = False
        self._cached_customers = {}
        self._cached_products = {}
        self._session = None
        self._logger.info("Disconnected from Stripe")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "account_id": self._account_id,
            "account_email": self._account_email,
            "livemode": self._livemode,
            "cached_customers": len(self._cached_customers),
            "cached_products": len(self._cached_products),
            "webhook_configured": bool(self._webhook_secret),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("balance")
            return "object" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    def create_customer(self,
                        email: str = "",
                        name: str = "",
                        description: str = "",
                        phone: str = "",
                        address: Optional[Dict[str, Any]] = None,
                        metadata: Optional[Dict[str, str]] = None,
                        **kwargs: Any) -> StripeCustomer:
        data: Dict[str, Any] = {}
        if email:
            data["email"] = email
        if name:
            data["name"] = name
        if description:
            data["description"] = description
        if phone:
            data["phone"] = phone
        if address:
            for key, val in address.items():
                data[f"address[{key}]"] = val
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call("customers", data=data, http_method="POST")
        customer = self._parse_customer(result)
        if customer.id:
            self._cached_customers[customer.id] = customer
        return customer

    def get_customer(self, customer_id: str) -> StripeCustomer:
        if customer_id in self._cached_customers:
            return self._cached_customers[customer_id]

        result = self._api_call(f"customers/{customer_id}")
        customer = self._parse_customer(result)
        if customer.id:
            self._cached_customers[customer.id] = customer
        return customer

    def update_customer(self, customer_id: str,
                        email: Optional[str] = None,
                        name: Optional[str] = None,
                        description: Optional[str] = None,
                        metadata: Optional[Dict[str, str]] = None,
                        **kwargs: Any) -> StripeCustomer:
        data: Dict[str, Any] = {}
        if email is not None:
            data["email"] = email
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call(
            f"customers/{customer_id}", data=data, http_method="POST"
        )
        customer = self._parse_customer(result)
        if customer.id:
            self._cached_customers[customer.id] = customer
        return customer

    def delete_customer(self, customer_id: str) -> bool:
        result = self._api_call(
            f"customers/{customer_id}", http_method="DELETE"
        )
        deleted = result.get("deleted", False)
        if deleted and customer_id in self._cached_customers:
            del self._cached_customers[customer_id]
        return deleted

    def list_customers(self, limit: int = 10,
                       email: str = "",
                       starting_after: str = "",
                       ending_before: str = "",
                       **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if email:
            params["email"] = email
        if starting_after:
            params["starting_after"] = starting_after
        if ending_before:
            params["ending_before"] = ending_before
        params.update(kwargs)

        result = self._api_call("customers", params=params)
        customers_data = result.get("data", [])
        customers = [self._parse_customer(c) for c in customers_data]
        for c in customers:
            if c.id:
                self._cached_customers[c.id] = c

        return {
            "customers": customers,
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def _parse_customer(self, data: Dict[str, Any]) -> StripeCustomer:
        return StripeCustomer(
            id=data.get("id", ""),
            email=data.get("email", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            phone=data.get("phone", ""),
            address=data.get("address", {}),
            balance=data.get("balance", 0),
            currency=data.get("currency", ""),
            created=data.get("created", 0),
            default_source=data.get("default_source", "") or "",
            delinquent=data.get("delinquent", False),
            discount=data.get("discount"),
            invoice_prefix=data.get("invoice_prefix", ""),
            invoice_settings=data.get("invoice_settings", {}),
            livemode=data.get("livemode", False),
            metadata=data.get("metadata", {}),
            preferred_locales=data.get("preferred_locales", []),
            shipping=data.get("shipping"),
            tax_exempt=data.get("tax_exempt", "none"),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Charges and payments
    # ------------------------------------------------------------------

    def create_charge(self, amount: int,
                      currency: StripeCurrency = StripeCurrency.USD,
                      customer: str = "",
                      source: str = "",
                      description: str = "",
                      capture: bool = True,
                      metadata: Optional[Dict[str, str]] = None,
                      **kwargs: Any) -> StripeCharge:
        data: Dict[str, Any] = {
            "amount": amount,
            "currency": currency.value,
            "capture": str(capture).lower(),
        }
        if customer:
            data["customer"] = customer
        if source:
            data["source"] = source
        if description:
            data["description"] = description
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call("charges", data=data, http_method="POST")
        return self._parse_charge(result)

    def get_charge(self, charge_id: str) -> StripeCharge:
        result = self._api_call(f"charges/{charge_id}")
        return self._parse_charge(result)

    def capture_charge(self, charge_id: str,
                       amount: Optional[int] = None,
                       **kwargs: Any) -> StripeCharge:
        data: Dict[str, Any] = {}
        if amount is not None:
            data["amount"] = amount
        data.update(kwargs)

        result = self._api_call(
            f"charges/{charge_id}/capture", data=data, http_method="POST"
        )
        return self._parse_charge(result)

    def list_charges(self, limit: int = 10,
                     customer: str = "",
                     created: Optional[Dict[str, Any]] = None,
                     **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if customer:
            params["customer"] = customer
        if created:
            for key, val in created.items():
                params[f"created[{key}]"] = val
        params.update(kwargs)

        result = self._api_call("charges", params=params)
        charges_data = result.get("data", [])
        return {
            "charges": [self._parse_charge(c) for c in charges_data],
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def _parse_charge(self, data: Dict[str, Any]) -> StripeCharge:
        return StripeCharge(
            id=data.get("id", ""),
            amount=data.get("amount", 0),
            amount_captured=data.get("amount_captured", 0),
            amount_refunded=data.get("amount_refunded", 0),
            application=data.get("application", "") or "",
            application_fee=data.get("application_fee", "") or "",
            balance_transaction=data.get("balance_transaction", "") or "",
            captured=data.get("captured", False),
            currency=StripeCurrency(data.get("currency", "usd")),
            customer=data.get("customer", "") or "",
            description=data.get("description", ""),
            disputed=data.get("disputed", False),
            failure_code=data.get("failure_code", ""),
            failure_message=data.get("failure_message", ""),
            fraud_details=data.get("fraud_details", {}),
            invoice=data.get("invoice", "") or "",
            livemode=data.get("livemode", False),
            metadata=data.get("metadata", {}),
            paid=data.get("paid", False),
            payment_intent=data.get("payment_intent", "") or "",
            payment_method=data.get("payment_method", "") or "",
            receipt_email=data.get("receipt_email", ""),
            receipt_number=data.get("receipt_number", ""),
            receipt_url=data.get("receipt_url", ""),
            refunded=data.get("refunded", False),
            shipping=data.get("shipping"),
            statement_descriptor=data.get("statement_descriptor", ""),
            status=data.get("status", ""),
            created=data.get("created", 0),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def create_subscription(self, customer_id: str,
                            items: List[Dict[str, Any]],
                            default_payment_method: str = "",
                            coupon: str = "",
                            trial_period_days: Optional[int] = None,
                            metadata: Optional[Dict[str, str]] = None,
                            **kwargs: Any) -> StripeSubscription:
        data: Dict[str, Any] = {"customer": customer_id}
        for i, item in enumerate(items):
            for key, val in item.items():
                data[f"items[{i}][{key}]"] = val
        if default_payment_method:
            data["default_payment_method"] = default_payment_method
        if coupon:
            data["coupon"] = coupon
        if trial_period_days is not None:
            data["trial_period_days"] = trial_period_days
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call(
            "subscriptions", data=data, http_method="POST"
        )
        return self._parse_subscription(result)

    def get_subscription(self, subscription_id: str) -> StripeSubscription:
        result = self._api_call(f"subscriptions/{subscription_id}")
        return self._parse_subscription(result)

    def update_subscription(self, subscription_id: str,
                            items: Optional[List[Dict[str, Any]]] = None,
                            cancel_at_period_end: Optional[bool] = None,
                            metadata: Optional[Dict[str, str]] = None,
                            **kwargs: Any) -> StripeSubscription:
        data: Dict[str, Any] = {}
        if items:
            for i, item in enumerate(items):
                for key, val in item.items():
                    data[f"items[{i}][{key}]"] = val
        if cancel_at_period_end is not None:
            data["cancel_at_period_end"] = str(cancel_at_period_end).lower()
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call(
            f"subscriptions/{subscription_id}",
            data=data,
            http_method="POST",
        )
        return self._parse_subscription(result)

    def cancel_subscription(self, subscription_id: str,
                            prorate: bool = True,
                            invoice_now: bool = False) -> StripeSubscription:
        data: Dict[str, Any] = {
            "prorate": str(prorate).lower(),
            "invoice_now": str(invoice_now).lower(),
        }
        result = self._api_call(
            f"subscriptions/{subscription_id}",
            data=data,
            http_method="DELETE",
        )
        return self._parse_subscription(result)

    def list_subscriptions(self, limit: int = 10,
                           customer: str = "",
                           status: str = "all",
                           **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "status": status}
        if customer:
            params["customer"] = customer
        params.update(kwargs)

        result = self._api_call("subscriptions", params=params)
        subs_data = result.get("data", [])
        return {
            "subscriptions": [self._parse_subscription(s) for s in subs_data],
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def _parse_subscription(self, data: Dict[str, Any]) -> StripeSubscription:
        items = data.get("items", {}).get("data", []) if data.get("items") else []
        return StripeSubscription(
            id=data.get("id", ""),
            customer=data.get("customer", "") or "",
            status=StripeSubscriptionStatus(data.get("status", "incomplete")),
            current_period_start=data.get("current_period_start", 0),
            current_period_end=data.get("current_period_end", 0),
            start_date=data.get("start_date", 0),
            ended_at=data.get("ended_at"),
            trial_start=data.get("trial_start"),
            trial_end=data.get("trial_end"),
            cancel_at=data.get("cancel_at"),
            canceled_at=data.get("canceled_at"),
            cancel_at_period_end=data.get("cancel_at_period_end", False),
            items=items,
            latest_invoice=data.get("latest_invoice", "") or "",
            default_payment_method=data.get("default_payment_method", "") or "",
            default_source=data.get("default_source", "") or "",
            default_tax_rates=data.get("default_tax_rates", []),
            discount=data.get("discount"),
            metadata=data.get("metadata", {}),
            pause_collection=data.get("pause_collection"),
            payment_settings=data.get("payment_settings", {}),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------

    def create_invoice(self, customer_id: str,
                       auto_advance: bool = True,
                       collection_method: str = "charge_automatically",
                       description: str = "",
                       metadata: Optional[Dict[str, str]] = None,
                       **kwargs: Any) -> StripeInvoice:
        data: Dict[str, Any] = {
            "customer": customer_id,
            "auto_advance": str(auto_advance).lower(),
            "collection_method": collection_method,
        }
        if description:
            data["description"] = description
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call("invoices", data=data, http_method="POST")
        return self._parse_invoice(result)

    def get_invoice(self, invoice_id: str) -> StripeInvoice:
        result = self._api_call(f"invoices/{invoice_id}")
        return self._parse_invoice(result)

    def finalize_invoice(self, invoice_id: str,
                         auto_advance: bool = True) -> StripeInvoice:
        result = self._api_call(
            f"invoices/{invoice_id}/finalize",
            data={"auto_advance": str(auto_advance).lower()},
            http_method="POST",
        )
        return self._parse_invoice(result)

    def pay_invoice(self, invoice_id: str,
                    **kwargs: Any) -> StripeInvoice:
        result = self._api_call(
            f"invoices/{invoice_id}/pay",
            data=kwargs,
            http_method="POST",
        )
        return self._parse_invoice(result)

    def void_invoice(self, invoice_id: str) -> StripeInvoice:
        result = self._api_call(
            f"invoices/{invoice_id}/void", http_method="POST"
        )
        return self._parse_invoice(result)

    def list_invoices(self, limit: int = 10,
                      customer: str = "",
                      status: str = "",
                      subscription: str = "",
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if customer:
            params["customer"] = customer
        if status:
            params["status"] = status
        if subscription:
            params["subscription"] = subscription
        params.update(kwargs)

        result = self._api_call("invoices", params=params)
        inv_data = result.get("data", [])
        return {
            "invoices": [self._parse_invoice(i) for i in inv_data],
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def _parse_invoice(self, data: Dict[str, Any]) -> StripeInvoice:
        lines = data.get("lines", {}).get("data", []) if data.get("lines") else []
        return StripeInvoice(
            id=data.get("id", ""),
            customer=data.get("customer", "") or "",
            subscription=data.get("subscription", "") or "",
            status=StripeInvoiceStatus(data.get("status", "draft")),
            amount_due=data.get("amount_due", 0),
            amount_paid=data.get("amount_paid", 0),
            amount_remaining=data.get("amount_remaining", 0),
            attempt_count=data.get("attempt_count", 0),
            attempted=data.get("attempted", False),
            auto_advance=data.get("auto_advance", True),
            billing_reason=data.get("billing_reason", ""),
            charge=data.get("charge", "") or "",
            currency=StripeCurrency(data.get("currency", "usd")),
            description=data.get("description", ""),
            due_date=data.get("due_date"),
            ending_balance=data.get("ending_balance", 0),
            hosted_invoice_url=data.get("hosted_invoice_url", ""),
            invoice_pdf=data.get("invoice_pdf", ""),
            lines=lines,
            metadata=data.get("metadata", {}),
            number=data.get("number", ""),
            paid=data.get("paid", False),
            receipt_number=data.get("receipt_number", ""),
            starting_balance=data.get("starting_balance", 0),
            statement_descriptor=data.get("statement_descriptor", ""),
            subtotal=data.get("subtotal", 0),
            tax=data.get("tax", 0),
            total=data.get("total", 0),
            webhooks_delivered_at=data.get("webhooks_delivered_at"),
            created=data.get("created", 0),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Products and prices
    # ------------------------------------------------------------------

    def create_product(self, name: str,
                       description: str = "",
                       active: bool = True,
                       metadata: Optional[Dict[str, str]] = None,
                       **kwargs: Any) -> StripeProduct:
        data: Dict[str, Any] = {
            "name": name,
            "active": str(active).lower(),
        }
        if description:
            data["description"] = description
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call("products", data=data, http_method="POST")
        product = self._parse_product(result)
        if product.id:
            self._cached_products[product.id] = product
        return product

    def get_product(self, product_id: str) -> StripeProduct:
        if product_id in self._cached_products:
            return self._cached_products[product_id]

        result = self._api_call(f"products/{product_id}")
        product = self._parse_product(result)
        if product.id:
            self._cached_products[product.id] = product
        return product

    def update_product(self, product_id: str,
                       name: Optional[str] = None,
                       description: Optional[str] = None,
                       active: Optional[bool] = None,
                       metadata: Optional[Dict[str, str]] = None,
                       **kwargs: Any) -> StripeProduct:
        data: Dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if active is not None:
            data["active"] = str(active).lower()
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call(
            f"products/{product_id}", data=data, http_method="POST"
        )
        product = self._parse_product(result)
        if product.id:
            self._cached_products[product.id] = product
        return product

    def list_products(self, limit: int = 10,
                      active: bool = True,
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "active": str(active).lower()}
        params.update(kwargs)

        result = self._api_call("products", params=params)
        products_data = result.get("data", [])
        products = [self._parse_product(p) for p in products_data]
        for p in products:
            if p.id:
                self._cached_products[p.id] = p
        return {
            "products": products,
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def create_price(self, product: str,
                     unit_amount: int,
                     currency: StripeCurrency = StripeCurrency.USD,
                     recurring: Optional[Dict[str, Any]] = None,
                     active: bool = True,
                     metadata: Optional[Dict[str, str]] = None,
                     **kwargs: Any) -> StripePrice:
        data: Dict[str, Any] = {
            "product": product,
            "unit_amount": unit_amount,
            "currency": currency.value,
            "active": str(active).lower(),
        }
        if recurring:
            for key, val in recurring.items():
                data[f"recurring[{key}]"] = val
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call("prices", data=data, http_method="POST")
        return self._parse_price(result)

    def get_price(self, price_id: str) -> StripePrice:
        result = self._api_call(f"prices/{price_id}")
        return self._parse_price(result)

    def list_prices(self, limit: int = 10,
                    product: str = "",
                    active: bool = True,
                    **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "active": str(active).lower()}
        if product:
            params["product"] = product
        params.update(kwargs)

        result = self._api_call("prices", params=params)
        prices_data = result.get("data", [])
        return {
            "prices": [self._parse_price(p) for p in prices_data],
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def _parse_product(self, data: Dict[str, Any]) -> StripeProduct:
        return StripeProduct(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            active=data.get("active", True),
            created=data.get("created", 0),
            images=data.get("images", []),
            livemode=data.get("livemode", False),
            metadata=data.get("metadata", {}),
            package_dimensions=data.get("package_dimensions"),
            shippable=data.get("shippable"),
            statement_descriptor=data.get("statement_descriptor", ""),
            tax_code=data.get("tax_code", "") or "",
            unit_label=data.get("unit_label", ""),
            updated=data.get("updated", 0),
            url=data.get("url", ""),
            extra=data,
        )

    def _parse_price(self, data: Dict[str, Any]) -> StripePrice:
        return StripePrice(
            id=data.get("id", ""),
            product=data.get("product", "") or "",
            unit_amount=data.get("unit_amount", 0),
            currency=StripeCurrency(data.get("currency", "usd")),
            active=data.get("active", True),
            billing_scheme=data.get("billing_scheme", "per_unit"),
            created=data.get("created", 0),
            livemode=data.get("livemode", False),
            lookup_key=data.get("lookup_key", ""),
            metadata=data.get("metadata", {}),
            nickname=data.get("nickname", ""),
            recurring=data.get("recurring"),
            tax_behavior=data.get("tax_behavior", ""),
            tiers=data.get("tiers"),
            tiers_mode=data.get("tiers_mode", ""),
            transform_quantity=data.get("transform_quantity"),
            type=data.get("type", "one_time"),
            unit_amount_decimal=data.get("unit_amount_decimal", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Payment intents
    # ------------------------------------------------------------------

    def create_payment_intent(self, amount: int,
                              currency: StripeCurrency = StripeCurrency.USD,
                              customer: str = "",
                              payment_method_types: Optional[List[str]] = None,
                              description: str = "",
                              capture_method: str = "automatic",
                              metadata: Optional[Dict[str, str]] = None,
                              **kwargs: Any) -> StripePaymentIntent:
        data: Dict[str, Any] = {
            "amount": amount,
            "currency": currency.value,
            "capture_method": capture_method,
        }
        if customer:
            data["customer"] = customer
        if payment_method_types:
            for i, pm in enumerate(payment_method_types):
                data[f"payment_method_types[{i}]"] = pm
        if description:
            data["description"] = description
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call(
            "payment_intents", data=data, http_method="POST"
        )
        return self._parse_payment_intent(result)

    def get_payment_intent(self, payment_intent_id: str) -> StripePaymentIntent:
        result = self._api_call(f"payment_intents/{payment_intent_id}")
        return self._parse_payment_intent(result)

    def confirm_payment_intent(self, payment_intent_id: str,
                               payment_method: str = "",
                               **kwargs: Any) -> StripePaymentIntent:
        data: Dict[str, Any] = {}
        if payment_method:
            data["payment_method"] = payment_method
        data.update(kwargs)

        result = self._api_call(
            f"payment_intents/{payment_intent_id}/confirm",
            data=data,
            http_method="POST",
        )
        return self._parse_payment_intent(result)

    def capture_payment_intent(self, payment_intent_id: str,
                               amount_to_capture: Optional[int] = None,
                               **kwargs: Any) -> StripePaymentIntent:
        data: Dict[str, Any] = {}
        if amount_to_capture is not None:
            data["amount_to_capture"] = amount_to_capture
        data.update(kwargs)

        result = self._api_call(
            f"payment_intents/{payment_intent_id}/capture",
            data=data,
            http_method="POST",
        )
        return self._parse_payment_intent(result)

    def cancel_payment_intent(self, payment_intent_id: str,
                              cancellation_reason: str = "") -> StripePaymentIntent:
        data = {"cancellation_reason": cancellation_reason} if cancellation_reason else {}
        result = self._api_call(
            f"payment_intents/{payment_intent_id}/cancel",
            data=data,
            http_method="POST",
        )
        return self._parse_payment_intent(result)

    def _parse_payment_intent(self, data: Dict[str, Any]) -> StripePaymentIntent:
        return StripePaymentIntent(
            id=data.get("id", ""),
            amount=data.get("amount", 0),
            amount_capturable=data.get("amount_capturable", 0),
            amount_details=data.get("amount_details", {}),
            amount_received=data.get("amount_received", 0),
            application=data.get("application", "") or "",
            application_fee_amount=data.get("application_fee_amount"),
            automatic_payment_methods=data.get("automatic_payment_methods"),
            canceled_at=data.get("canceled_at"),
            cancellation_reason=data.get("cancellation_reason", ""),
            capture_method=data.get("capture_method", "automatic"),
            client_secret=data.get("client_secret", ""),
            confirmation_method=data.get("confirmation_method", "automatic"),
            created=data.get("created", 0),
            currency=StripeCurrency(data.get("currency", "usd")),
            customer=data.get("customer", "") or "",
            description=data.get("description", ""),
            invoice=data.get("invoice", "") or "",
            latest_charge=data.get("latest_charge", "") or "",
            livemode=data.get("livemode", False),
            metadata=data.get("metadata", {}),
            next_action=data.get("next_action"),
            on_behalf_of=data.get("on_behalf_of", "") or "",
            payment_method=data.get("payment_method", "") or "",
            payment_method_types=data.get("payment_method_types", []),
            processing=data.get("processing"),
            receipt_email=data.get("receipt_email", ""),
            review=data.get("review", "") or "",
            setup_future_usage=data.get("setup_future_usage", ""),
            shipping=data.get("shipping"),
            statement_descriptor=data.get("statement_descriptor", ""),
            statement_descriptor_suffix=data.get("statement_descriptor_suffix", ""),
            status=StripePaymentIntentStatus(
                data.get("status", "requires_payment_method")
            ),
            transfer_data=data.get("transfer_data"),
            transfer_group=data.get("transfer_group", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Refunds
    # ------------------------------------------------------------------

    def create_refund(self, charge: str = "",
                      payment_intent: str = "",
                      amount: Optional[int] = None,
                      reason: str = "",
                      metadata: Optional[Dict[str, str]] = None,
                      **kwargs: Any) -> StripeRefund:
        data: Dict[str, Any] = {}
        if charge:
            data["charge"] = charge
        if payment_intent:
            data["payment_intent"] = payment_intent
        if amount is not None:
            data["amount"] = amount
        if reason:
            data["reason"] = reason
        if metadata:
            for key, val in metadata.items():
                data[f"metadata[{key}]"] = val
        data.update(kwargs)

        result = self._api_call("refunds", data=data, http_method="POST")
        return self._parse_refund(result)

    def get_refund(self, refund_id: str) -> StripeRefund:
        result = self._api_call(f"refunds/{refund_id}")
        return self._parse_refund(result)

    def list_refunds(self, limit: int = 10,
                     charge: str = "",
                     payment_intent: str = "",
                     **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if charge:
            params["charge"] = charge
        if payment_intent:
            params["payment_intent"] = payment_intent
        params.update(kwargs)

        result = self._api_call("refunds", params=params)
        refunds_data = result.get("data", [])
        return {
            "refunds": [self._parse_refund(r) for r in refunds_data],
            "has_more": result.get("has_more", False),
            "url": result.get("url", ""),
        }

    def _parse_refund(self, data: Dict[str, Any]) -> StripeRefund:
        return StripeRefund(
            id=data.get("id", ""),
            amount=data.get("amount", 0),
            balance_transaction=data.get("balance_transaction", "") or "",
            charge=data.get("charge", "") or "",
            created=data.get("created", 0),
            currency=StripeCurrency(data.get("currency", "usd")),
            description=data.get("description", ""),
            failure_balance_transaction=data.get("failure_balance_transaction", "") or "",
            failure_reason=data.get("failure_reason", ""),
            metadata=data.get("metadata", {}),
            next_action=data.get("next_action"),
            payment_intent=data.get("payment_intent", "") or "",
            reason=data.get("reason", ""),
            receipt_number=data.get("receipt_number", ""),
            status=StripeRefundStatus(data.get("status", "pending")),
            transfer_reversal=data.get("transfer_reversal", "") or "",
            extra=data,
        )

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, payload: str,
                       stripe_signature: str,
                       webhook_secret: str = "",
                       tolerance: int = 300) -> bool:
        """
        Verify a Stripe webhook signature.

        Args:
            payload: Raw request body.
            stripe_signature: Stripe-Signature header value.
            webhook_secret: Webhook signing secret.
            tolerance: Maximum age of the event in seconds.

        Returns:
            True if the webhook is verified.
        """
        secret = webhook_secret or self._webhook_secret
        if not secret:
            return False

        elements = {}
        for item in stripe_signature.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                elements[k.strip()] = v.strip()

        timestamp = elements.get("t", "0")
        signatures = [v for k, v in elements.items() if k.startswith("v")]

        if not timestamp or not signatures:
            return False

        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > tolerance:
                return False
        except (ValueError, TypeError):
            return False

        signed_payload = f"{timestamp}.{payload}"
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        for sig in signatures:
            if hmac.compare_digest(sig, expected):
                return True
        return False

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  data: Optional[Dict[str, Any]] = None,
                  http_method: str = "GET") -> Dict[str, Any]:
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"

        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Accept": "application/json",
            "Stripe-Version": "2024-06-20",
        }

        body = None
        if data:
            from urllib.parse import urlencode
            body = urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        self._log_request(http_method, url, headers, body.decode("utf-8") if body else None)

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                data=body,
                headers=headers,
                method=http_method,
            )

            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                self._log_response(
                    http_method, url, response.status, response_body
                )
                return json.loads(response_body)

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Stripe",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Stripe API key",
                    status_code=e.code,
                    response_body=response_body,
                )
            raise self._handle_error(
                f"HTTP error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=response_body,
            )
        except (IntegrationError, RateLimitError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request error: {e}")

    def __repr__(self) -> str:
        return (
            f"<StripeAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"account={self._account_id or 'unknown'} "
            f"mode={'live' if self._livemode else 'test'}>"
        )
