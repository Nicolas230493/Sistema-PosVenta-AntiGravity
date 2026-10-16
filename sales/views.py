from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, FileResponse, JsonResponse
from django.views.decorators.http import require_POST
from products.models import Product, PriceList, ProductPrice
from customers.models import Customer
from finance.models import CashSession, PaymentMethod
from core.decorators import manager_required
from .models import Sale, SaleReturn, SaleReturnDetail, Promotion
from .services import SaleError, create_sale, DEFAULT_CUSTOMER_DNI
from django.conf import settings
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
                price_list_id=request.POST.get('price_list'),
                discount_amount=request.POST.get('discount_amount'),
                surcharge_amount=request.POST.get('surcharge_amount'),
                points_redeemed=request.POST.get('points_redeemed'),
                client_uuid=request.POST.get('client_uuid'),
            )
            request.session['last_sale_id'] = sale.id
            if created:
                messages.success(request, f"Venta #{sale.id} registrada ({sale.payment_method.name}).")
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
    customers = Customer.objects.all()
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
    """Catálogo JSON para la copia local (IndexedDB) del POS offline."""
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
    return JsonResponse({'generated_at': timezone.now().isoformat(), 'products': products})


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
                cart=entry.get('cart'),
                customer_id=entry.get('customer_id'),
                payment_method_id=entry.get('payment_method'),
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


@login_required
def service_worker(request):
    """Sirve el Service Worker con alcance /pos/ (debe vivir bajo esa ruta)."""
    sw_path = settings.BASE_DIR / 'static' / 'js' / 'pos-sw.js'
    response = HttpResponse(sw_path.read_text(encoding='utf-8'), content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    return response

@login_required
def sale_list(request):
    sales = Sale.objects.select_related('user', 'customer', 'payment_method', 'cash_session').order_by('-fecha_hora')
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
                        return_details_text.append(f"{detail.product.name} ({qty_to_return})")
                
                if total_return_amount == 0:
                    raise Exception("Debe seleccionar al menos un producto para devolver")
                
                sale_return.total_amount = total_return_amount
                sale_return.save()
                
                ActivityLog.objects.create(
                    user=request.user,
                    action=f"Devolución procesada #{sale_return.id} de Venta #{sale.id}",
                    module="Ventas",
                    details=", ".join(return_details_text)
                )
                
                messages.success(request, f"Devolución #{sale_return.id} procesada correctamente.")
                return redirect('sales:sale_list')
                
        except Exception as e:
            messages.error(request, f"Error al procesar devolución: {str(e)}")
            
    return render(request, 'sales/sale_return.html', {'sale': sale})
