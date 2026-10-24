"""
SPEC-QA-13 §13.1: prueba de integración "Día Completo en Pollería".

Recorre un turno de punta a punta a través de las mismas URLs que usa la interfaz:
apertura de caja, compras con CPP, venta fraccional con pago mixto, venta en descubierto
(desposte dinámico), sincronización offline idempotente y cierre ciego con Ganancia Neta Real.
"""
import json
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finance.models import CashSession, PaymentMethod
from finance.services import net_profit
from products.models import InventoryMovement, Product
from sales.models import Sale, SalePayment
from suppliers.models import Supplier


class DiaCompletoEnPolleriaE2ETests(TestCase):

    def setUp(self):
        self.cajero = User.objects.create_user(username='cajera_e2e', password='pass12345')
        self.cajero.groups.add(Group.objects.get_or_create(name='Cajeros')[0])
        self.cajero.user_permissions.add(Permission.objects.get(codename='add_sale', content_type__app_label='sales'))
        self.duenio = User.objects.create_superuser(username='duenio_e2e', password='pass12345')

        self.efectivo = PaymentMethod.objects.create(name='Efectivo')
        self.transferencia = PaymentMethod.objects.create(name='Transferencia', is_digital=True)
        self.proveedor = Supplier.objects.create(name='Avícola del Sur')
        self.pollo = Product.objects.create(sku='POLLO', name='Pollo Entero', unit='kg',
                                            price=Decimal('4000.00'), cost_price=Decimal('0'), stock=Decimal('0'))
        self.alitas = Product.objects.create(sku='ALITAS', name='Alitas', unit='kg',
                                             price=Decimal('3000.00'), cost_price=Decimal('1500.00'), stock=Decimal('0'))

    # ---------- helpers ----------
    def _comprar(self, kilos, costo, factura):
        return self.client.post(reverse('products:purchase_create'), {
            'supplier_id': self.proveedor.id,
            'invoice_number': factura,
            'items_data': json.dumps([{'product_id': self.pollo.id, 'qty': kilos, 'cost': costo}]),
        })

    def _vender(self, producto, kilos, payments):
        response = self.client.post(reverse('sales:pos'), {
            'cart_data': json.dumps([{'id': producto.id, 'qty': 1, 'weight': kilos}]),
            'payment_method': str(self.efectivo.id),
            'payments_data': json.dumps(payments),
            'price_list': 'default',
        }, headers={'X-POS-Ajax': '1'})
        self.assertEqual(response.status_code, 200, response.content)
        return Sale.objects.get(pk=response.json()['sale_id'])

    def _sync(self, sales):
        return self.client.post(reverse('sales_sync'), json.dumps({'sales': sales}), content_type='application/json')

    # ---------- turno completo ----------
    def test_dia_completo_en_polleria(self):
        self.client.force_login(self.cajero)

        # 1. Apertura de caja con $10.000
        self.client.post(reverse('finance:open_cash'), {'amount': '10000'})
        session = CashSession.objects.get(is_open=True)
        self.assertEqual(session.initial_amount, Decimal('10000.00'))

        # 2. Compra de mercadería: 10 kg a $2.000 + 10 kg a $2.500 -> CPP $2.250/kg, stock 20.000
        self._comprar('10', '2000', 'A-0001')
        self._comprar('10', '2500', 'A-0002')
        self.pollo.refresh_from_db()
        self.assertEqual(self.pollo.cost_price, Decimal('2250.00'))
        self.assertEqual(self.pollo.stock, Decimal('20.000'))

        # 3. Venta fraccional 1.500 kg ($6.000) con pago mixto mitad efectivo / mitad transferencia
        venta_mixta = self._vender(self.pollo, '1.500', [
            {'method_id': self.efectivo.id, 'amount': '3000.00'},
            {'method_id': self.transferencia.id, 'amount': '3000.00', 'reference': 'TRF-123'},
        ])
        self.assertEqual(venta_mixta.total_amount, Decimal('6000.00'))
        pagos = {p.payment_method.name: p.amount for p in SalePayment.objects.filter(sale=venta_mixta)}
        self.assertEqual(pagos, {'Efectivo': Decimal('3000.00'), 'Transferencia': Decimal('3000.00')})
        self.assertEqual(venta_mixta.details.get().unit_cost, Decimal('2250.00'))
        self.pollo.refresh_from_db()
        self.assertEqual(self.pollo.stock, Decimal('18.500'))

        # 4. Venta en descubierto: 2.000 kg de Alitas con stock 0.000 -> AUTO_ADJUST_IN 2.000 kg
        venta_alitas = self._vender(self.alitas, '2.000', [{'method_id': self.efectivo.id, 'amount': '6000.00'}])
        ajuste = InventoryMovement.objects.get(movement_type='AUTO_ADJUST_IN', product=self.alitas)
        self.assertEqual(ajuste.quantity, Decimal('2.000'))
        self.assertEqual(ajuste.reference, f'Venta #{venta_alitas.id}')
        self.alitas.refresh_from_db()
        self.assertEqual(self.alitas.stock, Decimal('0.000'))

        # 5. Sincronización offline: el mismo client_uuid llega dos veces en el lote y se reenvía
        venta_offline = {
            'client_uuid': '7d0c5a9e-1f4b-4c1e-9a7b-2f3e4d5c6b7a',
            'created_at': timezone.now().isoformat(),
            'items': [{'id': self.pollo.id, 'qty': 1, 'weight': '1.000'}],
            'payment_method_id': str(self.efectivo.id),
            'total_amount': '4000.00',
            'price_list': 'default',
        }
        primera = self._sync([venta_offline, dict(venta_offline)]).json()['results']
        reenvio = self._sync([venta_offline]).json()['results']
        self.assertEqual([r['status'] for r in primera], ['created', 'duplicate'])
        self.assertEqual(reenvio[0]['status'], 'duplicate')
        sincronizada = Sale.objects.get(client_uuid=venta_offline['client_uuid'])
        self.assertEqual(sincronizada.total_amount, Decimal('4000.00'))
        self.assertEqual(sincronizada.cash_session_id, session.id)
        self.assertEqual(Sale.objects.count(), 3)
        self.pollo.refresh_from_db()
        self.assertEqual(self.pollo.stock, Decimal('17.500'))

        # Gasto del turno cargado por el dueño (resta del efectivo y de la Ganancia Neta)
        self.client.force_login(self.duenio)
        self.client.post(reverse('finance:add_expense'), {'amount': '500', 'description': 'Hielo'})

        # 6. Cierre ciego: la cajera no ve el esperado y declara $100 menos de efectivo
        #    Esperado efectivo = 10.000 + 3.000 + 6.000 + 4.000 - 500 = 22.500 | digital = 3.000
        self.client.force_login(self.cajero)
        pantalla = self.client.get(reverse('finance:close_cash'))
        self.assertTrue(pantalla.context['blind'])
        self.assertNotIn('expected', pantalla.context)
        self.assertNotContains(pantalla, '22500')
        self.assertNotContains(pantalla, '22.500')
        self.assertContains(pantalla, 'Alitas')  # panel de ajustes forzosos del turno

        self.client.post(reverse('finance:close_cash'), {
            'declared_cash': '22400.00', 'declared_digital': '3000.00', 'notes': 'Fin del día',
        })
        session.refresh_from_db()
        self.assertFalse(session.is_open)
        self.assertEqual(session.expected_cash, Decimal('22500.00'))
        self.assertEqual(session.expected_digital, Decimal('3000.00'))
        self.assertEqual(session.difference, Decimal('-100.00'))  # faltante

        # Ganancia Neta Real = Ventas Netas - CMV - Gastos - Mermas
        #   Ventas: 6.000 + 6.000 + 4.000 = 16.000
        #   CMV: 1,5 x 2.250 + 2 x 1.500 + 1 x 2.250 = 8.625
        #   16.000 - 8.625 - 500 - 0 = 6.875
        tz = timezone.get_current_timezone()
        hoy = timezone.localdate()
        reporte = net_profit(datetime.combine(hoy, time.min, tzinfo=tz),
                             datetime.combine(hoy + timedelta(days=1), time.min, tzinfo=tz))
        self.assertEqual(reporte['net_sales'], Decimal('16000.00'))
        self.assertEqual(reporte['cmv'], Decimal('8625.00'))
        self.assertEqual(reporte['expenses'], Decimal('500.00'))
        self.assertEqual(reporte['losses'], Decimal('0.00'))
        self.assertEqual(reporte['net_profit'], Decimal('6875.00'))

        # El dueño ve el mismo resultado en pantalla
        self.client.force_login(self.duenio)
        pantalla_duenio = self.client.get(reverse('finance:profit_report'))
        self.assertEqual(pantalla_duenio.context['report']['net_profit'], Decimal('6875.00'))
