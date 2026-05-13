from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, FileResponse
from products.models import Product, InventoryMovement, PriceList, ProductPrice
from customers.models import Customer
from finance.models import PaymentMethod, CashSession
from .models import Sale, SaleDetail, SaleReturn, SaleReturnDetail, Promotion
from core.models import ActivityLog
from django.utils import timezone
from .utils import generate_sale_pdf, generate_thermal_ticket, generate_total_sales_report
import json
from decimal import Decimal

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
    sales = Sale.objects.all().order_by('-date')
    buffer = generate_total_sales_report(sales)
    return FileResponse(buffer, as_attachment=True, filename="Reporte_Ventas_ImpulsoSmart.pdf")

@login_required
def pos_view(request):
    # Validar sesión de caja activa
    active_session = CashSession.objects.filter(user=request.user, is_open=True).exists()
    if not active_session:
        messages.warning(request, "Debes abrir una sesión de caja antes de realizar ventas.")
        return redirect('finance:cash_dashboard')

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
        cart_data = request.POST.get('cart_data')
        customer_id = request.POST.get('customer_id')
        payment_method_id = request.POST.get('payment_method')
        discount_amount = Decimal(request.POST.get('discount_amount', 0) or 0)
        surcharge_amount = Decimal(request.POST.get('surcharge_amount', 0) or 0)
        
        # Puntos
        points_to_redeem = int(request.POST.get('points_redeemed', 0) or 0)
        points_discount = Decimal(request.POST.get('points_discount', 0) or 0)
        
        try:
            cart = json.loads(cart_data)
            if not cart:
                messages.error(request, "El carrito está vacío")
                return redirect('sales:pos')

            customer = Customer.objects.get(id=customer_id) if customer_id else default_customer
            payment_method = PaymentMethod.objects.get(id=payment_method_id)

            # Validación de Puntos
            if points_to_redeem > 0:
                if not customer:
                    raise Exception("Debe seleccionar un cliente para canjear puntos.")
                if customer.points < points_to_redeem:
                    raise Exception(f"El cliente no tiene suficientes puntos ({customer.points}).")

            # Calcular total estimado para validación de límite de crédito
            temp_total = Decimal(0)
            for item in cart:
                temp_total += Decimal(item['price']) * int(item['qty'])
            final_total_est = temp_total - discount_amount - points_discount + surcharge_amount

            # Validación de Límite de Crédito
            if payment_method.name == 'Cuenta Corriente' and customer:
                if customer.limite_credito > 0: # 0 significa sin límite o sin crédito habilitado
                    if (customer.balance + final_total_est) > customer.limite_credito:
                        raise Exception(f"Límite de crédito excedido. Saldo actual: ${customer.balance}, Límite: ${customer.limite_credito}")
                elif customer.dni_cuit == '00000000':
                    raise Exception("No se puede fiar al Consumidor Final.")

            with transaction.atomic():
                sale = Sale.objects.create(
                    user=request.user,
                    customer=customer,
                    total_amount=0,
                    tax_amount=0,
                    discount_amount=discount_amount,
                    points_redeemed=points_to_redeem,
                    points_discount=points_discount,
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

                total_items = 0
                total_tax = 0
                total_promo_disc = 0
                for item in cart:
                    product = Product.objects.select_for_update().get(id=item['id'])
                    qty = int(item['qty'])
                    if product.stock < qty:
                        raise Exception(f"Stock insuficiente para {product.name}")
                    
                    product.stock -= qty
                    product.save()
                    
                    # Precios desde el carrito (incluyen promos aplicadas en JS)
                    price = Decimal(item['discounted_price'])
                    original_price = Decimal(item['original_price_at_sale'])
                    promo_savings = (original_price - price) * qty
                    total_promo_disc += promo_savings
                    
                    subtotal = price * qty
                    # Cálculo de IVA: el precio ya incluye IVA, lo desglosamos
                    tax_rate = product.tax_rate
                    tax_item = subtotal - (subtotal / (1 + (tax_rate / 100)))
                    
                    total_items += subtotal
                    total_tax += tax_item
                    
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
                
                final_total = total_items - discount_amount - points_discount + surcharge_amount
                sale.total_amount = final_total
                sale.tax_amount = total_tax
                sale.promo_discount = total_promo_disc
                sale.save()
                
                # Note: signals.py handles CC balance now. 
                # But signals.py uses payment_method.name == 'Cuenta Corriente'.
                
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
    sales = Sale.objects.all().order_by('-date')
    return render(request, 'sales/sale_list.html', {'sales': sales})

@login_required
def whatsapp_ticket(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if not sale.customer or not sale.customer.phone:
        messages.error(request, "El cliente no tiene un teléfono registrado.")
        return redirect('sales:sale_list')
    
    resumen = f"*Ticket Digital - Impulso Smart*\n"
    resumen += f"Venta #{sale.id} - {sale.date.strftime('%d/%m/%Y')}\n"
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
