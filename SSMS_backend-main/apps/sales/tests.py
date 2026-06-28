from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import Store, Tenant, User
from apps.catalog.models import Inventory, Product, StockBatch, StockMovement
from apps.customers.models import Customer
from apps.sales.models import CustomerBalanceEntry, Sale


class SaleWorkflowApiTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Demo Tenant", slug="demo")
        self.store = Store.objects.create(
            tenant=self.tenant,
            name="Main Shop",
            code="MAIN",
            is_active=True,
        )
        self.seller = User.objects.create_user(
            username="seller",
            password="seller-pass-123",
            tenant=self.tenant,
            store=self.store,
            role=User.Role.SELLER,
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="admin",
            password="admin-pass-123",
            tenant=self.tenant,
            store=self.store,
            role=User.Role.ADMIN,
            is_active=True,
        )
        self.seller.assigned_stores.add(self.store)
        self.admin.assigned_stores.add(self.store)

        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Water",
            barcode="WATER-001",
            unit_price=Decimal("5.00"),
        )
        self.inventory = Inventory.objects.create(
            tenant=self.tenant,
            product=self.product,
            store=self.store,
            stock_units=10,
            is_active=True,
        )
        self.batch = StockBatch.objects.create(
            tenant=self.tenant,
            inventory=self.inventory,
            product=self.product,
            units_received=10,
            units_remaining=10,
        )

    def authenticate_seller(self):
        self.client.force_authenticate(user=self.seller)

    def sale_payload(self, client_reference="sale-001"):
        return {
            "client_reference": client_reference,
            "store": self.store.id,
            "customer": {
                "name": "Ana",
                "reference": "ana-001",
                "phone": "+258 84 000 0001",
            },
            "note": "",
            "items": [
                {
                    "product": self.product.id,
                    "quantity_units": 3,
                    "amount_paid": "10.00",
                    "payment_status": "partial",
                    "pickup_status": "now",
                    "note": "Pays the rest later.",
                }
            ],
        }

    def test_sale_deducts_stock_allocates_batch_and_updates_customer_debt(self):
        self.authenticate_seller()

        response = self.client.post("/api/v1/sales/", self.sale_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["gross_total"], "15.00")
        self.assertEqual(response.data["paid_total"], "10.00")
        self.assertEqual(response.data["debt_total"], "5.00")
        self.assertEqual(response.data["status"], Sale.Status.OPEN)

        self.inventory.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(self.inventory.stock_units, 7)
        self.assertEqual(self.batch.units_remaining, 7)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.MovementType.SALE).count(), 1)

        customer = Customer.objects.get(reference="ana-001")
        self.assertEqual(customer.debt_balance, Decimal("5.00"))
        self.assertEqual(customer.credit_balance, Decimal("0.00"))
        self.assertEqual(
            CustomerBalanceEntry.objects.get(customer=customer).amount,
            Decimal("5.00"),
        )

    def test_replaying_same_client_reference_returns_existing_sale_without_new_stock_deduction(self):
        self.authenticate_seller()
        payload = self.sale_payload(client_reference="retry-safe-001")

        first_response = self.client.post("/api/v1/sales/", payload, format="json")
        second_response = self.client.post("/api/v1/sales/", payload, format="json")

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.data["id"], first_response.data["id"])
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.MovementType.SALE).count(), 1)

        self.inventory.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(self.inventory.stock_units, 7)
        self.assertEqual(self.batch.units_remaining, 7)

    def test_rejects_inconsistent_pay_later_amount_without_changing_stock(self):
        self.authenticate_seller()
        payload = self.sale_payload(client_reference="bad-payment-001")
        payload["items"][0]["payment_status"] = "later"
        payload["items"][0]["amount_paid"] = "1.00"

        response = self.client.post("/api/v1/sales/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Sale.objects.count(), 0)
        self.inventory.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(self.inventory.stock_units, 10)
        self.assertEqual(self.batch.units_remaining, 10)

    def test_seller_cannot_update_sale_or_directly_create_customer(self):
        self.authenticate_seller()
        sale_response = self.client.post("/api/v1/sales/", self.sale_payload(), format="json")
        sale_id = sale_response.data["id"]

        sale_patch_response = self.client.patch(
            f"/api/v1/sales/{sale_id}/",
            {"note": "seller edit"},
            format="json",
        )
        customer_create_response = self.client.post(
            "/api/v1/customers/",
            {"name": "Manual Customer", "reference": "manual-001"},
            format="json",
        )

        self.assertEqual(sale_patch_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(customer_create_response.status_code, status.HTTP_403_FORBIDDEN)
