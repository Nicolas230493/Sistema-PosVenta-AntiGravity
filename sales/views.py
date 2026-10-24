from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, FileResponse, JsonResponse
from django.views.decorators.http import require_POST
from products.models import Product, PriceList, ProductPrice, InventoryMovement
from customers.models import Customer, CurrentAccount
from finance.models import CashSession, PaymentMethod
from core.decorators import manager_required
from .models import Sale, SaleReturn, SaleReturnDetail, Promotion
from .services import (
    SaleError, create_sale, DEFAULT_CUSTOMER_DNI, cancel_sale, complete_reservation,
    sale_current_account_amount,
)
from django.conf import settings
from django.middleware.csrf import get_token
from django.templatetags.static import static
from django.urls import reverse
from core.models import ConfiguracionSistema
from django.utils import timezone

from .utils import generate_sale_pdf, generate_thermal_ticket, generate_total_sales_report
import json
from decimal import Decimal, InvalidOperation

SYNC_BATCH_LIMIT = 200


@login_required
def export_sale_pdf(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=Venta_{sale.id}.pdf'
    generate_sale_pdf(response, sale)
    return response

@login_required
def export_thermal_ticket(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    buffer = generate_thermal_ticket(sale)
    return FileResponse(buffer, as_attachment=False, filename=f"Ticket_{sale.id}.pdf")

@manager_required
def export_consolidated_report(request):
    sales = Sale.objects.all().order_by('-fecha_hora')
    buffer = generate_total_sales_report(sales)
    return FileResponse(buffer, as_attachment=True, filename="Reporte_Ventas_ImpulsoSmart.pdf")

@login_required
@permission_required('sales.add_sale', raise_exception=True)
def pos_view(request):
    # SPEC-POS-03: el turno activo se determina solo por estado, nunca por fecha
    active_session = CashSession.get_active()

    if request.method == 'POST':
        is_ajax = request.headers.get('X-POS-Ajax') == '1'
        try:
            sale, created = create_sale(
                user=request.user,
                cart=request.POST.get('cart_data'),
                customer_id=request.POST.get('customer_id'),
                payment_method_id=request.POST.get('payment_method'),
                payments=request.POST.get('payments_data'),
                status=request.POST.get('sale_status') or Sale.STATUS_COMPLETED,
                price_list_id=request.POST.get('price_list'),
                discount_amount=request.POST.get('discount_amount'),
                surcharge_amount=request.POST.get('surcharge_amount'),
                points_redeemed=request.POST.get('points_redeemed'),
                client_uuid=request.POST.get('client_uuid'),
            )
            request.session['last_sale_id'] = sale.id
            if created and sale.status == Sale.STATUS_RESERVED:
                messages.success(request, f"Pedido #{sale.id} registrado. Seña: ${sale.amount_paid} — Saldo pendiente: ${sale.balance_due}.")
            elif created:
                methods = ' + '.join(p.payment_method.name for p in sale.payments.select_related('payment_method'))
                messages.success(request, f"Venta #{sale.id} registrada ({methods or 'sin cobro'}).")
            else:
                messages.info(request, f"Venta #{sale.id} ya estaba registrada.")
            if is_ajax:
                return JsonResponse({'ok': True, 'sale_id': sale.id, 'created': created})
        except SaleError as e:
            messages.error(request, f"Error al procesar venta: {e}")
            if is_ajax:
                return JsonResponse({'ok': False, 'error': str(e)}, status=400)
        return redirect('sales:pos')

    if not active_session:
        return render(request, 'sales/pos_no_session.html')

    products = Product.objects.all().order_by('name')
    customers = Customer.objects.select_related('price_list')
    payment_methods = PaymentMethod.objects.filter(active=True)
    price_lists = PriceList.objects.filter(active=True)
    default_customer = Customer.objects.filter(dni_cuit=DEFAULT_CUSTOMER_DNI).first()
    last_sale_id = request.session.pop('last_sale_id', None)
    has_messages = len(messages.get_messages(request)) > 0

    today = timezone.localdate()
    active_promos = Promotion.objects.filter(active=True, start_date__lte=today, end_date__gte=today)

    # Preparar datos de promociones para JS
    promos_data = []
    for p in active_promos:
        promos_data.append({
            'id': p.id,
            'name': p.name,
            'type': p.promo_type,
            'discount': float(p.discount_percentage),
            'fixed_qty': p.fixed_qty,
            'fixed_price': float(p.fixed_price),
            'day': p.day_of_week,
            'product_ids': list(p.products.values_list('id', flat=True)),
            'category_ids': list(p.categories.values_list('id', flat=True))
        })

    product_prices = {}
    for pp in ProductPrice.objects.filter(price_list__active=True):
        product_prices.setdefault(pp.product_id, {})[pp.price_list_id] = float(pp.price)

    response = render(request, 'sales/pos.html', {
        'products': products,
        'customers': customers,
        'payment_methods': payment_methods,
        'price_lists': price_lists,
        'default_customer': default_customer,
        'last_sale_id': last_sale_id,
        'active_session': active_session,
        'session_from_previous_day': timezone.localtime(active_session.start_date).date() < today,
        'product_prices_json': json.dumps(product_prices),
        'promotions_json': json.dumps(promos_data)
    })
    # Las respuestas con mensajes o ticket pendiente no deben quedar en la caché offline
    if last_sale_id or has_messages:
        response['X-POS-No-Cache'] = '1'
    return response


@login_required
def pos_catalog(request):
    """
    Catálogo JSON para la copia local (IndexedDB) del POS offline.
    Incluye un token CSRF vigente: si el POS se abrió desde la copia en caché (con el token de
    una sesión anterior), pos-offline.js lo reemplaza antes de enviar ventas o sincronizar.
    """
    products = [
        {
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'barcode': p.barcode or '',
            'unit': p.unit,
            'price': str(p.price),
            'stock': str(p.stock),
            'category_id': p.category_id,
        }
        for p in Product.objects.all().order_by('name')
    ]
    response = JsonResponse({
        'generated_at': timezone.now().isoformat(),
        'csrf_token': get_token(request),
        'products': products,
    })
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@permission_required('sales.add_sale', raise_exception=True)
@require_POST
def sync_sales(request):
    """
    SPEC-SYNC-05 §6.2: recibe el lote de ventas guardadas offline.
    Idempotente por client_uuid; cada venta se procesa en su propia transacción.
    """
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'JSON inválido.'}, status=400)

    batch = payload.get('sales') if isinstance(payload, dict) else None
    if not isinstance(batch, list):
        return JsonResponse({'error': "Se esperaba la clave 'sales' con una lista."}, status=400)
    if len(batch) > SYNC_BATCH_LIMIT:
        return JsonResponse({'error': f'Máximo {SYNC_BATCH_LIMIT} ventas por lote.'}, status=400)

    results = []
    for entry in batch:
        if not isinstance(entry, dict):
            results.append({'client_uuid': None, 'status': 'error', 'error': 'Formato de venta inválido.'})
            continue
        client_uuid = entry.get('client_uuid')
        if not client_uuid:
            results.append({'client_uuid': None, 'status': 'error', 'error': 'Falta client_uuid.'})
            continue
        try:
            sale, created = create_sale(
                user=request.user,
                cart=entry.get('items') or entry.get('cart'),  # SPEC-PWA-11: 'items' (cola nueva) o 'cart'
                customer_id=entry.get('customer_id'),
                payment_method_id=entry.get('payment_method_id') or entry.get('payment_method'),
                payments=entry.get('payments'),
                status=entry.get('status') or Sale.STATUS_COMPLETED,
                price_list_id=entry.get('price_list'),
                discount_amount=entry.get('discount_amount'),
                surcharge_amount=entry.get('surcharge_amount'),
                points_redeemed=entry.get('points_redeemed'),
                client_uuid=client_uuid,
                client_created_at=entry.get('created_at'),
            )
            results.append({
                'client_uuid': client_uuid,
                'status': 'created' if created else 'duplicate',
                'sale_id': sale.id,
            })
        except SaleError as e:
            results.append({'client_uuid': client_uuid, 'status': 'error', 'error': str(e)})

    return JsonResponse({'results': results})


def service_worker(request):
    """
    SPEC-PWA-11 §11.1: Service Worker servido desde la raíz (/sw.js) con alcance '/'.
    Es público (no contiene datos) para que el navegador pueda buscar actualizaciones
    aunque la sesión haya expirado. /pos/sw.js sigue respondiendo para instalaciones previas.
    """
    sw_path = settings.BASE_DIR / 'static' / 'js' / 'pos-sw.js'
    script = sw_path.read_text(encoding='utf-8')
    if settings.DEBUG:
        # Sin hash en los estáticos: los archivos propios van network-first para ver cambios al instante
        script = script.replace('const DEV_MODE = false;', 'const DEV_MODE = true;', 1)
    response = HttpResponse(script, content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/'
    return response


def web_manifest(request):
    """SPEC-PWA-11 §11.1: manifiesto de la PWA en /manifest.json (instalable, abre directo en el POS)."""
    config = ConfiguracionSistema.get_config()
    name = config.nombre_empresa or 'Impulso Smart POS'
    manifest = {
        'name': name,
        'short_name': name[:12],
        'description': 'Punto de venta con operación sin conexión',
        'start_url': reverse('sales:pos'),
        'scope': '/',
        'display': 'standalone',
        'orientation': 'any',
        'background_color': '#f4f7fe',
        'theme_color': '#4318ff',
        'lang': 'es',
        'icons': [{
            'src': static('img/pos-icon.svg'),
            'sizes': 'any',
            'type': 'image/svg+xml',
            'purpose': 'any',
        }],
    }
    response = JsonResponse(manifest, json_dumps_params={'ensure_ascii': False})
    response['Content-Type'] = 'application/manifest+json'
    response['Cache-Control'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/'
    return response

@login_required
def ticket_escpos(request, pk):
    """SPEC-POS-08 §8.3: ticket HTML para impresora térmica 58/80mm con impresión automática."""
    sale = get_object_or_404(
        Sale.objects.select_related('customer', 'user'), pk=pk
    )
    # SPEC-OPS-14 §14.2: ?width=58 (también se acepta el parámetro corto ?w=58)
    width = '58' if (request.GET.get('width') or request.GET.get('w')) == '58' else '80'
    return render(request, 'sales/ticket_escpos.html', {
        'sale': sale,
        'details': sale.details.select_related('product'),
        'payments': sale.payments.select_related('payment_method'),
        'width': width,
        'autoprint': request.GET.get('print', '1') != '0',
    })


@login_required
def reservation_list(request):
    """SPEC-POS-08 §8.2: pedidos / apartados con seña pendientes de entrega."""
    reservations = Sale.objects.filter(status=Sale.STATUS_RESERVED).select_related(
        'customer', 'user'
    ).prefetch_related('payments__payment_method', 'details__product').order_by('fecha_hora')
    return render(request, 'sales/reservation_list.html', {
        'reservations': reservations,
        'payment_methods': PaymentMethod.objects.filter(active=True),
    })


@login_required
@permission_required('sales.add_sale', raise_exception=True)
@require_POST
def reservation_complete(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    try:
        payments = request.POST.get('payments_data')
        if not payments:
            payments = [{
                'method_id': request.POST.get('payment_method'),
                'amount': request.POST.get('amount'),
                'reference': request.POST.get('reference', ''),
            }]
        complete_reservation(sale=sale, user=request.user, payments=payments)
        messages.success(request, f"Pedido #{sale.id} cobrado y entregado.")
    except SaleError as e:
        messages.error(request, f"No se pudo completar el pedido: {e}")
    return redirect('sales:reservation_list')


@login_required
def sale_list(request):
    sales = Sale.objects.select_related('user', 'customer', 'payment_method', 'cash_session').prefetch_related('payments__payment_method').order_by('-fecha_hora')
    return render(request, 'sales/sale_list.html', {'sales': sales})

@login_required
def whatsapp_ticket(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if not sale.customer or not sale.customer.phone:
        messages.error(request, "El cliente no tiene un teléfono registrado.")
        return redirect('sales:sale_list')
    
    resumen = f"*Ticket Digital - Impulso Smart*\n"
    resumen += f"Venta #{sale.id} - {sale.fecha_hora.strftime('%d/%m/%Y')}\n"
    resumen += f"--------------------------\n"
    for item in sale.details.all():
        resumen += f"{item.product.name} x{item.quantity}: ${item.subtotal}\n"
    
    if sale.discount_amount > 0: resumen += f"Descuento: -${sale.discount_amount}\n"
    if sale.points_discount > 0: resumen += f"Canje Puntos: -${sale.points_discount}\n"
    
    resumen += f"--------------------------\n"
    resumen += f"*TOTAL: ${sale.total_amount}*\n"
    resumen += f"Gracias por su compra!"
    
    import urllib.parse
    link = f"https://wa.me/{sale.customer.phone}?text={urllib.parse.quote(resumen)}"
    return redirect(link)

from core.models import ActivityLog

@manager_required
def sale_return(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if sale.is_cancelled:
        messages.error(request, "No se pueden realizar devoluciones de una venta anulada.")
        return redirect('sales:sale_list')
    if sale.status == Sale.STATUS_RESERVED:
        messages.error(request, "Un pedido con seña pendiente no admite devoluciones: anúlelo o complételo primero.")
        return redirect('sales:sale_list')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                total_return_amount = 0
                sale_return = SaleReturn.objects.create(
                    sale=sale,
                    reason=request.POST.get('reason'),
                    total_amount=0,
                    user=request.user
                )
                
                return_details_text = []
                for detail in sale.details.all():
                    try:
                        qty_to_return = Decimal(request.POST.get(f'qty_{detail.id}') or '0')
                    except InvalidOperation:
                        raise Exception(f"Cantidad inválida para {detail.product.name}")
                    if qty_to_return < 0:
                        raise Exception("La cantidad a devolver no puede ser negativa.")
                    if qty_to_return > 0:
                        if qty_to_return > detail.quantity:
                            raise Exception(f"No se puede devolver más de lo vendido ({detail.product.name})")
                        
                        item_return_amount = detail.price * qty_to_return
                        total_return_amount += item_return_amount
                        
                        SaleReturnDetail.objects.create(
                            sale_return=sale_return,
                            product=detail.product,
                            quantity=qty_to_return,
                            price_at_return=detail.price
                        )
                        # SPEC-AUD-STOCK-06 §6.2.7: Reversión atómica de stock y movimiento SALE_CANCEL_IN
                        if detail.product:
                            prod = Product.objects.select_for_update().get(pk=detail.product_id)
                            prod.stock += qty_to_return
                            prod.save()
                            InventoryMovement.objects.create(
                                product=prod,
                                quantity=qty_to_return,
                                movement_type='SALE_CANCEL_IN',
                                reference=f"Devolución Venta #{sale.id}",
                                reason=f"Devolución parcial/total #{sale_return.id}: {sale_return.reason or 'Sin motivo'}",
                                user=request.user
                            )
                        return_details_text.append(f"{detail.product.name} ({qty_to_return})")
                
                if total_return_amount == 0:
                    raise Exception("Debe seleccionar al menos un producto para devolver")
                
                sale_return.total_amount = total_return_amount
                sale_return.save()

                # Reversión de Cuenta Corriente por la parte fiada de la venta
                cc_amount = min(total_return_amount, sale_current_account_amount(sale))
                if sale.customer and cc_amount > 0:
                    customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
                    customer.balance -= cc_amount
                    customer.save(update_fields=['balance'])
                    CurrentAccount.objects.create(
                        customer=customer,
                        amount=cc_amount,
                        entry_type='CREDIT',
                        reference=f"Devolución #{sale_return.id}",
                        balance_after=customer.balance,
                    )
                
                ActivityLog.objects.create(
                    user=request.user,
                    action=f"Devolución procesada #{sale_return.id} de Venta #{sale.id}",
                    module="Ventas",
                    details=", ".join(return_details_text)
                )
                
                messages.success(request, f"Devolución #{sale_return.id} procesada correctamente. Stock reincorporado.")
                return redirect('sales:sale_list')
                
        except Exception as e:
            messages.error(request, f"Error al procesar devolución: {str(e)}")
            
    return render(request, 'sales/sale_return.html', {'sale': sale})


@manager_required
def sale_cancel(request, pk):
    """SPEC-AUD-STOCK-06 §6.2.7: Anulación de venta con reversión de stock y SALE_CANCEL_IN."""
    sale = get_object_or_404(Sale, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason')
        if not reason or not reason.strip():
            messages.error(request, "Debe ingresar el motivo de la anulación.")
            return redirect('sales:sale_list')
        try:
            cancel_sale(sale=sale, user=request.user, reason=reason.strip())
            messages.success(request, f"Venta #{sale.id} anulada con éxito. Stock reincorporado íntegramente.")
        except SaleError as e:
            messages.error(request, f"Error al anular la venta: {e}")
        return redirect('sales:sale_list')
    return render(request, 'sales/sale_confirm_cancel.html', {'sale': sale})
