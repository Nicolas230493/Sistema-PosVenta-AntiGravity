from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, FileResponse
from products.models import Product, InventoryMovement, PriceList, ProductPrice
from customers.models import Customer
from finance.models import PaymentMethod
from core.models import ActivityLog, TurnoCaja
from .models import Sale, SaleDetail, SaleReturn, SaleReturnDetail, Promotion
from django.utils import timezone

from .utils import generate_sale_pdf, generate_thermal_ticket, generate_total_sales_report
import json
from decimal import Decimal


def _money(value, default='0'):
    return Decimal(str(value or default))


def _apply_server_promotions(product, qty, unit_price, active_promos, today):
    price = unit_price
    django_day = today.weekday()

    for promo in active_promos:
        is_target = promo.products.filter(id=product.id).exists()
        if not is_target and product.category_id:
            is_target = promo.categories.filter(id=product.category_id).exists()
        if not is_target:
            continue

        if promo.promo_type == 'PERCENT':
            price -= price * (promo.discount_percentage / Decimal('100'))
        elif promo.promo_type == 'DAY_DISCOUNT' and promo.day_of_week == django_day:
            price -= price * (promo.discount_percentage / Decimal('100'))
        elif promo.promo_type == 'FIXED_QTY' and promo.fixed_qty > 0 and qty >= promo.fixed_qty:
            num_packs = qty // promo.fixed_qty
            remainder = qty % promo.fixed_qty
            price = ((num_packs * promo.fixed_price) + (remainder * unit_price)) / qty

    return price.quantize(Decimal('0.01'))


def _price_for_product(product, price_list_id):
    if price_list_id and price_list_id != 'default':
        custom_price = ProductPrice.objects.filter(
            product=product,
            price_list_id=price_list_id,
            price_list__active=True
        ).values_list('price', flat=True).first()
        if custom_price is not None:
            return custom_price
    return product.price

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

@login_required
def export_consolidated_report(request):
    sales = Sale.objects.all().order_by('-fecha_hora')
    buffer = generate_total_sales_report(sales)
    return FileResponse(buffer, as_attachment=True, filename="Reporte_Ventas_ImpulsoSmart.pdf")

@login_required
@permission_required('sales.add_sale', raise_exception=True)
def pos_view(request):
    # Validar turno de caja activo
    active_turno = TurnoCaja.objects.filter(usuario=request.user, estado='ABIERTO').first()
    if not active_turno:
        messages.warning(request, "Debes abrir un turno de caja antes de realizar ventas.")
        return redirect('dashboard')

    products = Product.objects.filter(stock__gt=0)
    customers = Customer.objects.all()
    payment_methods = PaymentMethod.objects.filter(active=True)
    price_lists = PriceList.objects.filter(active=True)
    default_customer = Customer.objects.filter(dni_cuit='00000000').first()
    last_sale_id = request.session.pop('last_sale_id', None)
    
    # Promociones Activas
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

    if request.method == 'POST':
        try:
            cart_data = request.POST.get('cart_data')
            customer_id = request.POST.get('customer_id')
            payment_method_id = request.POST.get('payment_method')
            price_list_id = request.POST.get('price_list')
            discount_amount = _money(request.POST.get('discount_amount'))
            surcharge_amount = _money(request.POST.get('surcharge_amount'))
            points_to_redeem = int(request.POST.get('points_redeemed', 0) or 0)

            if not cart_data:
                raise ValueError("No se enviaron datos del carrito.")

            cart = json.loads(cart_data)
            if not cart:
                raise ValueError("El carrito está vacío.")

            if discount_amount < 0 or surcharge_amount < 0:
                raise ValueError("Descuentos y recargos no pueden ser negativos.")
            if points_to_redeem < 0:
                raise ValueError("Los puntos a canjear no pueden ser negativos.")

            if not payment_method_id:
                raise ValueError("Debe seleccionar un método de pago.")

            customer = Customer.objects.get(id=customer_id) if customer_id else default_customer
            payment_method = PaymentMethod.objects.get(id=payment_method_id)
            points_discount = Decimal(points_to_redeem)

            # Validación de Puntos
            if points_to_redeem > 0:
                if not customer:
                    raise ValueError("Debe seleccionar un cliente para canjear puntos.")
                if customer.points < points_to_redeem:
                    raise ValueError(f"El cliente no tiene suficientes puntos ({customer.points}).")

            with transaction.atomic():
                # Re-verificar turno activo dentro de la transacción
                active_turno = TurnoCaja.objects.filter(usuario=request.user, estado='ABIERTO').first()
                if not active_turno:
                    raise Exception("Turno de caja cerrado durante la transacción.")

                sale_items = []
                total_items = Decimal('0.00')
                total_tax = Decimal('0.00')
                total_promo_disc = Decimal('0.00')

                for item in cart:
                    product = Product.objects.select_for_update().get(id=item['id'])
                    qty = int(item['qty'])
                    if qty <= 0:
                        raise Exception("La cantidad vendida debe ser mayor a cero.")
                    if product.stock < qty:
                        raise Exception(f"Stock insuficiente para {product.name}")

                    original_price = _price_for_product(product, price_list_id)
                    price = _apply_server_promotions(product, qty, original_price, active_promos, today)
                    promo_savings = (original_price - price) * qty
                    subtotal = price * qty
                    tax_rate = product.tax_rate
                    tax_item = subtotal - (subtotal / (1 + (tax_rate / 100)))

                    total_items += subtotal
                    total_tax += tax_item
                    total_promo_disc += promo_savings
                    sale_items.append((product, qty, price, tax_rate, tax_item, subtotal))

                final_total = total_items - discount_amount - points_discount + surcharge_amount
                if final_total < 0:
                    raise Exception("El total de la venta no puede ser negativo.")

                # Validación de Límite de Crédito
                if payment_method.name == 'Cuenta Corriente' and customer:
                    if customer.limite_credito > 0: # 0 significa sin límite o sin crédito habilitado
                        if (customer.balance + final_total) > customer.limite_credito:
                            raise Exception(f"Límite de crédito excedido. Saldo actual: ${customer.balance}, Límite: ${customer.limite_credito}")
                    elif customer.dni_cuit == '00000000':
                        raise Exception("No se puede fiar al Consumidor Final.")

                sale = Sale.objects.create(
                    user=request.user,
                    customer=customer,
                    turno=active_turno,
                    total_amount=final_total,
                    tax_amount=total_tax,
                    discount_amount=discount_amount,
                    points_redeemed=points_to_redeem,
                    points_discount=points_discount,
                    promo_discount=total_promo_disc,
                    surcharge_amount=surcharge_amount,
                    payment_method=payment_method
                )
                
                # Descontar puntos del cliente si hubo canje
                if points_to_redeem > 0:
                    customer.points -= points_to_redeem
                    customer.save()
                    ActivityLog.objects.create(
                        user=request.user,
                        action=f"Canje de {points_to_redeem} puntos - Venta #{sale.id}",
                        module="Fidelización",
                        details=f"Cliente: {customer.full_name}, Descuento: ${points_discount}"
                    )

                for product, qty, price, tax_rate, tax_item, subtotal in sale_items:
                    product.stock -= qty
                    product.save()
                    
                    SaleDetail.objects.create(
                        sale=sale,
                        product=product,
                        quantity=qty,
                        price=price,
                        cost_price_at_sale=product.cost_price,
                        tax_rate=tax_rate,
                        tax_amount=tax_item,
                        subtotal=subtotal
                    )
                    
                    InventoryMovement.objects.create(
                        product=product,
                        quantity=qty,
                        movement_type='OUT',
                        reference=f"Venta #{sale.id}",
                        user=request.user
                    )
                
                request.session['last_sale_id'] = sale.id
                messages.success(request, f"Venta #{sale.id} registrada ({payment_method.name}).")
                return redirect('sales:pos')
                
        except Exception as e:
            messages.error(request, f"Error al procesar venta: {str(e)}")
            return redirect('sales:pos')
            
    # Preparar datos de precios especiales para el JS
    product_prices = {}
    for pp in ProductPrice.objects.filter(price_list__active=True):
        if pp.product_id not in product_prices:
            product_prices[pp.product_id] = {}
        product_prices[pp.product_id][pp.price_list_id] = float(pp.price)

    return render(request, 'sales/pos.html', {
        'products': products,
        'customers': customers,
        'payment_methods': payment_methods,
        'price_lists': price_lists,
        'default_customer': default_customer,
        'last_sale_id': last_sale_id,
        'product_prices_json': json.dumps(product_prices),
        'promotions_json': json.dumps(promos_data)
    })

@login_required
def sale_list(request):
    sales = Sale.objects.all().order_by('-fecha_hora')
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

@login_required
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
                    qty_to_return = int(request.POST.get(f'qty_{detail.id}', 0))
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
